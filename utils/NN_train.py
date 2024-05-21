import torch
import time, os
from torch.optim.lr_scheduler import ExponentialLR
import numpy as np
import gc
import multiprocessing as mp
import matplotlib as mpl
import matplotlib.pyplot as plt 
mpl.rcParams['lines.markersize'] = 3
import copy
from scipy.optimize import linear_sum_assignment

torch.set_default_dtype(torch.float64)
torch.set_num_threads(1)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from .constants import *
from .pp_func import plotPP, plot_training_validation_cost, plotBandStruct, plotBandStruct_reorder, plot_mc_cost

def print_and_inspect_gradients(model, filename=None, show=False): 
    if (filename is None) and show: 
        for name, param in model.named_parameters():
            if param.grad is not None:
                print(f'Parameter: {name}, Gradient shape: {param.grad.shape}')
                print(f'Gradient values:\n{param.grad}\n')
            else:
                print(f'Parameter: {name}, Gradient: None (no gradient computed)\n')
    elif (filename is not None) and show: 
        with open(filename, 'w') as f:
            for name, param in model.named_parameters():
                if param.grad is not None:
                    f.write(f'Parameter: {name}, Gradient shape: {param.grad.shape}\n')
                    grad_str = np.array2string(param.grad.numpy(), precision=5, suppress_small=True, max_line_width=999999, threshold=99*99)
                    f.write(f'Gradient values:\n{grad_str}\n\n')
                else:
                    f.write(f'Parameter: {name}, Gradient: None (no gradient computed)\n\n')    


def print_and_inspect_NNParams(model, filename=None, show=False): 
    if (filename is None) and show: 
        for name, param in model.named_parameters():
            print(f'Parameter: {name}, Tensor shape: {param.shape}')
            print(f'Parameter values:\n{param}\n')
    elif (filename is not None) and show: 
        with open(filename, 'w') as f:
            for name, param in model.named_parameters():
                f.write(f'Parameter: {name}, Tensor shape: {param.shape}\n')
                tensor_str = np.array2string(param.detach().numpy(), precision=5, suppress_small=True, max_line_width=999999, threshold=99*99)
                f.write(f'Gradient values:\n{tensor_str}\n\n')


def weighted_mse_bandStruct(bandStruct_hat, bulkSystem): 
    bandWeights = bulkSystem.bandWeights
    kptWeights = bulkSystem.kptWeights
    nkpt = bulkSystem.getNKpts()
    nBands = bulkSystem.nBands
    if (len(bandWeights)!=nBands) or (len(kptWeights)!=nkpt): 
        raise ValueError("bandWeights or kptWeights lengths aren't correct. ")
        
    newBandWeights = bandWeights.view(1, -1).expand(nkpt, -1)
    newKptWeights = kptWeights.view(-1, 1).expand(-1, nBands)
    
    MSE = torch.sum((bandStruct_hat-bulkSystem.expBandStruct)**2 * newBandWeights * newKptWeights)
    return MSE


def weighted_mse_energiesAtKpt(calcEnergiesAtKpt, bulkSystem, kidx): 
    bandWeights = bulkSystem.bandWeights
    nBands = bulkSystem.nBands
    if (len(calcEnergiesAtKpt)!=nBands): 
        raise ValueError("CalculatedEnergiesAtKpt is of different length as nBands. Can't calculated MSE.")

    MSE = torch.sum((calcEnergiesAtKpt-bulkSystem.expBandStruct[kidx])**2 * bandWeights)
    return MSE


def evalBS_noGrad(model, BSplotFilename, runName, NNConfig, hams, systems, cachedMats_info=None, writeBS=False): 
    if (model is not None): 
        print(f"{runName}: Evaluating band structures using the NN-pp model. ")
        model.eval()
    else:
        print(f"{runName}: Evaluating band structures using the old Zunger function form. ")
    
    plot_bandStruct_list = []
    totalMSE = 0
    for iSys, sys in enumerate(systems):
        if (model is not None): 
            hams[iSys].NN_locbool = True
            hams[iSys].set_NNmodel(model)
        else: 
            hams[iSys].NN_locbool = False

        start_time = time.time()
        with torch.no_grad():
            evalBS = hams[iSys].calcBandStruct_noGrad(cachedMats_info)
        evalBS.detach_()
        end_time = time.time()
        print(f"{runName}: Finished evaluating {iSys}-th band structure with no gradient... Elapsed time: {(end_time - start_time):.2f} seconds")
        if writeBS: 
            if not BSplotFilename.endswith('_plotBS.png'):
                raise ValueError("BSplotFilename must end with '_plotBS.png' to write BS.dat files. ")
            else:
                write_BS_filename = BSplotFilename.replace('_plotBS.png', f'_BS_sys{iSys}.dat')
            kptDistInputs_vertical = sys.kptDistInputs.view(-1, 1)
            write_tensor = torch.cat((kptDistInputs_vertical, evalBS), dim=1)
            np.savetxt(write_BS_filename, write_tensor, fmt='%.5f')
            print(f"{runName}: Wrote BS to file {write_BS_filename}. ")
        plot_bandStruct_list.append(sys.expBandStruct)
        plot_bandStruct_list.append(evalBS)
        totalMSE += weighted_mse_bandStruct(evalBS, sys)
    fig = plotBandStruct(systems, plot_bandStruct_list, NNConfig['SHOWPLOTS'])
    print(f"{runName}: totalMSE = {totalMSE:f}")
    fig.suptitle(f"{runName}: totalMSE = {totalMSE:f}")
    fig.savefig(BSplotFilename)
    plt.close('all')
    torch.cuda.empty_cache()
    return totalMSE


def calcEigValsAtK_wGrad_parallel(kidx, ham, bulkSystem, criterion_singleKpt, optimizer, model, cachedMats_info=None):
    """
    loop over kidx
    The rest of the arguments are "constants" / "constant functions" for a single kidx
    For performance, it is recommended that the ham in the argument doesn't have SOmat and NLmat initialized. 
    """
    singleKptGradients = {}
    calcEnergies = ham.calcEigValsAtK(kidx, cachedMats_info, requires_grad=True)

    systemKptLoss = criterion_singleKpt(calcEnergies, bulkSystem, kidx)
    start_time = time.time() if ham.NNConfig['runtime_flag'] else None
    optimizer.zero_grad()
    systemKptLoss.backward()
    end_time = time.time() if ham.NNConfig['runtime_flag'] else None
    print(f"loss_backward, elapsed time: {(end_time - start_time):.2f} seconds") if ham.NNConfig['runtime_flag'] else None
    for name, param in model.named_parameters():
        if param.grad is not None:
            if name not in singleKptGradients:
                singleKptGradients[name] = param.grad.detach().clone() * bulkSystem.kptWeights[kidx]
            else: 
                singleKptGradients[name] += param.grad.detach().clone() * bulkSystem.kptWeights[kidx]
    trainLoss_systemKpt = systemKptLoss.detach().item() * bulkSystem.kptWeights[kidx]
    del systemKptLoss
    gc.collect()
    return singleKptGradients, trainLoss_systemKpt


def trainIter_naive(model, systems, hams, criterion_singleSystem, optimizer, cachedMats_info=None, runtime_flag=False):
    trainLoss = torch.tensor(0.0)
    for iSys, sys in enumerate(systems):
        hams[iSys].NN_locbool = True
        hams[iSys].set_NNmodel(model)

        NN_outputs = hams[iSys].calcBandStruct_withGrad(cachedMats_info)
        
        systemLoss = criterion_singleSystem(NN_outputs, sys)
        # print_and_inspect_gradients(model, show=True)
        trainLoss += systemLoss

    start_time = time.time() if runtime_flag else None
    optimizer.zero_grad()
    trainLoss.backward()
    # print_and_inspect_gradients(model, show=True)
    optimizer.step()
    end_time = time.time() if runtime_flag else None
    print(f"loss_backward + optimizer.step, elapsed time: {(end_time - start_time):.2f} seconds") if runtime_flag else None

    torch.cuda.empty_cache()
    return model, trainLoss


def extrapolate(y1, y2, y3, y4):
    first_derivative = y4 - y3
    second_derivative = y4 - 2 * y3 + y2
    third_derivative = (y4 - 2 * y3 + y2) - (y3 - 2 * y2 + y1)
    
    # more agressive extrapolation than Taylor expansion
    next_point = y4 + 1.3 * first_derivative + 0.5 * second_derivative + (1/6) * third_derivative
    
    return next_point


def reorder_smoothness_deg2_tensors(oldBS, max_band_switch=5):
    numKpts, numBands = oldBS.shape
    order_table = torch.zeros((numKpts, numBands), dtype=torch.long)
    extrapolated_points = oldBS.clone()
    currBS = oldBS.clone()
    
    for i in range(4):
        order_table[i, :] = torch.arange(numBands, dtype=torch.long)

    for i in range(4, numKpts):
        predicted_points = extrapolate(currBS[i-4], currBS[i-3], currBS[i-2], currBS[i-1])
        extrapolated_points[i, :] = predicted_points
        costMatrix = torch.abs(predicted_points.unsqueeze(0) - oldBS[i].unsqueeze(1))
        costMatrix = costMatrix.T
        
        # Add restriction
        for r in range(numBands):
            for s in range(numBands):
                if abs(r - s) > max_band_switch:
                    costMatrix[r, s] = 999999

        # Two-fold degeneracy
        M = torch.zeros((numBands // 2, numBands // 2), dtype=torch.float64)
        for r in range(numBands // 2):
            for s in range(numBands // 2):
                M[r, s] = max(costMatrix[2*r, 2*s] + costMatrix[2*r+1, 2*s+1], 
                              costMatrix[2*r, 2*s+1] + costMatrix[2*r+1, 2*s])
        
        row_ind, col_ind = linear_sum_assignment(M.cpu().detach().numpy())
        col_ind = torch.tensor(col_ind, dtype=torch.long, device=oldBS.device)
        col_ind_2deg = torch.zeros(numBands, dtype=torch.long, device=oldBS.device)
        col_ind_2deg[::2] = col_ind * 2
        col_ind_2deg[1::2] = col_ind * 2 + 1
        col_ind = col_ind_2deg
        
        order_table[i, :] = col_ind
        currBS[i, :] = oldBS[i, col_ind]

    return order_table, currBS, extrapolated_points


def trainIter_naive_smoothReorder(model, systems, hams, criterion_singleSystem, optimizer, cachedMats_info=None, runtime_flag=False):
    trainLoss = torch.tensor(0.0)
    for iSys, sys in enumerate(systems):
        hams[iSys].NN_locbool = True
        hams[iSys].set_NNmodel(model)

        NN_outputs = hams[iSys].calcBandStruct_withGrad(cachedMats_info)

        # reorder NN_outputs
        order_table, newBS, extrapolated_points = reorder_smoothness_deg2_tensors(NN_outputs)
        systemLoss = criterion_singleSystem(newBS, sys)
        trainLoss += systemLoss

    start_time = time.time() if runtime_flag else None
    optimizer.zero_grad()
    trainLoss.backward()
    optimizer.step()
    end_time = time.time() if runtime_flag else None
    print(f"loss_backward + optimizer.step, elapsed time: {(end_time - start_time):.2f} seconds") if runtime_flag else None

    torch.cuda.empty_cache()
    return model, trainLoss, order_table, newBS, extrapolated_points


def trainIter_separateKptGrad(model, systems, hams, NNConfig, criterion_singleKpt, optimizer, resultsFolder, cachedMats_info=None): 
    def merge_dicts(dicts):
        merged_dict = {}
        for d in dicts:
            for key in d:
                merged_dict[key] = merged_dict.get(key, 0) + d[key]
        return merged_dict
    
    trainLoss = 0.0
    total_gradients = {}
    for iSys, sys in enumerate(systems):
        trainLoss_system = 0.0
        gradients_system = {}
        hams[iSys].NN_locbool = True
        hams[iSys].set_NNmodel(model)

        if (NNConfig['num_cores']==0):   # No multiprocessing
            for kidx in range(sys.getNKpts()): 
                calcEnergies = hams[iSys].calcEigValsAtK(kidx, cachedMats_info, requires_grad=True)   # writeEVecsToFile=True, writeEVecsFolderName=resultsFolder)
                systemKptLoss = criterion_singleKpt(calcEnergies, sys, kidx)

                start_time = time.time() if NNConfig['runtime_flag'] else None
                optimizer.zero_grad()
                systemKptLoss.backward()
                end_time = time.time() if NNConfig['runtime_flag'] else None
                print(f"loss_backward, elapsed time: {(end_time - start_time):.2f} seconds") if NNConfig['runtime_flag'] else None

                for name, param in model.named_parameters():
                    if param.grad is not None:
                        if name not in gradients_system:
                            gradients_system[name] = param.grad.detach().clone() * sys.kptWeights[kidx]
                        else: 
                            gradients_system[name] += param.grad.detach().clone() * sys.kptWeights[kidx]
                trainLoss_system += systemKptLoss.detach().item() * sys.kptWeights[kidx]
                del systemKptLoss
                gc.collect()

        else: # multiprocessing
            optimizer.zero_grad()
            args_list = [(kidx, hams[iSys], sys, criterion_singleKpt, optimizer, model, cachedMats_info) for kidx in range(sys.getNKpts())]
            with mp.Pool(NNConfig['num_cores']) as pool:
                results_systemKpt = pool.starmap(calcEigValsAtK_wGrad_parallel, args_list)
                gradients_systemKpt, trainLoss_systemKpt = zip(*results_systemKpt)
            gc.collect()
            gradients_system = merge_dicts(gradients_systemKpt)
            trainLoss_system = torch.sum(torch.tensor(trainLoss_systemKpt))
        
        total_gradients = merge_dicts([total_gradients, gradients_system])
        trainLoss += trainLoss_system

    # Write the manually accumulated gradients and loss values back into the NN model
    optimizer.zero_grad()
    with torch.no_grad():
        for name, param in model.named_parameters():
            if name in total_gradients:
                param.grad = total_gradients[name].detach().clone()

    start_time = time.time() if NNConfig['runtime_flag'] else None
    optimizer.step()
    end_time = time.time() if NNConfig['runtime_flag'] else None
    print(f"optimizer step, elapsed time: {(end_time - start_time):.2f} seconds") if NNConfig['runtime_flag'] else None

    torch.cuda.empty_cache()
    # print_and_inspect_gradients(model, show=NNConfig['printGrad'])

    return model, trainLoss


def bandStruct_train_GPU(model, device, NNConfig, systems, hams, atomPPOrder, criterion_singleSystem, criterion_singleKpt, optimizer, scheduler, val_dataset, resultsFolder, cachedMats_info=None):
    training_COST=[]
    validation_COST=[]
    file_trainCost = open(f'{resultsFolder}final_training_cost.dat', "w")
    file_valCost = open(f'{resultsFolder}final_validation_cost.dat', "w")
    model.to(device)
    best_validation_loss = float('inf')
    no_improvement_count = 0
    
    for epoch in range(NNConfig['max_num_epochs']):
        
        # train
        model.train()
        if NNConfig['separateKptGrad']==0: 
            print("PLEASE MAKE SURE that the input band structure is given in the PHYSICAL order, similar to that from the smoothness criteria. \nPREFERABLY, please use 150 kpts. ")
            # model, trainLoss = trainIter_naive(model, systems, hams, criterion_singleSystem, optimizer, cachedMats_info, NNConfig['runtime_flag'])
            model, trainLoss, order_table, newBS, extrapolated_points = trainIter_naive_smoothReorder(model, systems, hams, criterion_singleSystem, optimizer, cachedMats_info, NNConfig['runtime_flag'])

            for bandIdx in range(newBS.shape[1]):
                fig, ax = plotBandStruct_reorder(newBS.detach().numpy(), bandIdx)
                ax.plot(np.arange(len(newBS)), extrapolated_points[:, bandIdx].detach().numpy(), "gx:", alpha=0.8, markersize=4)
                ax.set(ylim=(min(extrapolated_points[:, bandIdx])-0.1, max(extrapolated_points[:, bandIdx])+0.1))
                # plot_highlight_kpt(ax, [0,3,6,13,19,26,34,40,50,60,65,70,79,90,100,108])
                fig.savefig(f"{resultsFolder}epoch_{epoch+1}_newBand_{bandIdx}.png")
                fig.savefig(f"{resultsFolder}epoch_{epoch+1}_newBand_{bandIdx}.pdf")
                plt.close()

        else: 
            raise ValueError("""On this branch, only naiive training without parallelization is implemented. 
                             Parallel version of fitting won't be allowed. 
                             Please make sure to set 'separateKptGrad' keyword to False. """)
            model, trainLoss = trainIter_separateKptGrad(model, systems, hams, NNConfig, criterion_singleKpt, optimizer, resultsFolder, cachedMats_info)
        training_COST.append(trainLoss.item())
        file_trainCost.write(f"{epoch+1}  {trainLoss.item()}\n")
        print(f"Epoch [{epoch+1}/{NNConfig['max_num_epochs']}], training cost: {trainLoss.item():.4f}")
        if (epoch<=9) or ((epoch + 1) % NNConfig['plotEvery'] == 0):
            print_and_inspect_gradients(model, f'{resultsFolder}epoch_{epoch+1}_gradients.dat', show=True)
            print_and_inspect_NNParams(model, f'{resultsFolder}epoch_{epoch+1}_params.dat', show=True)

        # perturb the model
        if (NNConfig['perturbEvery']>0) and (epoch>0) and (epoch % NNConfig['perturbEvery']==0): 
            perturb_model(model, 0.10)
            print("WARNING: We have randomly perturbed all the params of the model by 10%. \n")

        # scheduler of learning rate
        if (epoch > 0) and (epoch % NNConfig['schedulerStep'] == 0):
            scheduler.step()

        # evaluation
        if (epoch + 1) % NNConfig['plotEvery'] == 0:
            model.eval()
            val_MSE = evalBS_noGrad(model, f'{resultsFolder}epoch_{epoch+1}_plotBS.png', f'epoch_{epoch+1}', NNConfig, hams, systems, cachedMats_info, writeBS=True)
            
            validation_COST.append(val_MSE)
            print(f"Epoch [{epoch+1}/{NNConfig['max_num_epochs']}], validation cost: {val_MSE:.4f}")
            file_valCost.write(f"{epoch+1}  {val_MSE}\n")
            
            model.cpu()
            fig = plotPP(atomPPOrder, val_dataset.q, val_dataset.q, val_dataset.vq_atoms, model(val_dataset.q), "ZungerForm", f"NN_{epoch+1}", ["-",":" ]*len(atomPPOrder), True, NNConfig['SHOWPLOTS']);
            fig.savefig(f'{resultsFolder}epoch_{epoch+1}_plotPP.png')
            model.to(device)

            torch.save(model.state_dict(), f'{resultsFolder}epoch_{epoch+1}_PPmodel.pth')
            torch.save(optimizer.state_dict(), f'{resultsFolder}epoch_{epoch+1}_AdamState.pth')
            torch.cuda.empty_cache()
        '''
        # Dynamic stopping: Stop training if no improvement for 'patience' epochs
        if val_MSE < best_validation_loss - 1e-4:
            best_validation_loss = val_MSE
            no_improvement_count = 0
        else:
            no_improvement_count += 1
        if no_improvement_count >= NNConfig['patience']:
            print(f"Early stopping at Epoch {epoch} due to lack of improvement.")
            break
        '''
        plt.close('all')
        torch.cuda.empty_cache()
    fig_cost = plot_training_validation_cost(training_COST, validation_COST, True, NNConfig['SHOWPLOTS']);
    fig_cost.savefig(resultsFolder + 'final_train_cost.png')
    torch.cuda.empty_cache()
    return (training_COST, validation_COST)


def perturb_model(model, percentage=0.0): 
    print(f"Perturbing the model by percentage: {percentage}")
    for param in model.parameters():
        perturbation = 1 + torch.rand_like(param) * (2 * percentage) - percentage
        param.data *= perturbation
    return model


def runMC_NN(model, NNConfig, systems, hams, atomPPOrder, val_dataset, resultsFolder, cachedMats_info=None):
    file_trainCost = open(f'{resultsFolder}MC_training_cost.dat', "w")
    file_trainCost.write("# iter      newLoss      accept?      currLoss\n")
    
    bestModel = model
    bestLoss = evalBS_noGrad(bestModel, f'{resultsFolder}mc_iter_0_plotBS.png', f'mc_iter_0', NNConfig, hams, systems, cachedMats_info)
    currModel = model
    currLoss = bestLoss
    trial_COST = [currLoss]
    accepted_COST = [currLoss]

    for iter in range(NNConfig['mc_iter']):
        print(f"\nIteration [{iter+1}/{NNConfig['mc_iter']}]: ")
        newModel = perturb_model(currModel, percentage=NNConfig['mc_percentage'])
        newLoss = evalBS_noGrad(newModel, f'{resultsFolder}mc_iter_{iter+1}_plotBS.png', f'mc_iter_{iter+1}', NNConfig, hams, systems, cachedMats_info)
        print(f"newLoss={newLoss.item():.4f}. ")

        mc_rand = np.exp(-1 * NNConfig['mc_beta'] * (np.sqrt(newLoss) - np.sqrt(currLoss)))
        mc_accept_bool = mc_rand > np.random.uniform(low=0.0, high=1.0)

        if newLoss < bestLoss:   # accept
            bestLoss = newLoss
            bestModel = newModel
            currLoss = newLoss
            currModel = newModel
            file_trainCost.write(f"{iter+1}    {newLoss.item()}    {1}    {currLoss.item()}\n")
            print(f"Accepted. currLoss={currLoss.item():.4f}")
            print_and_inspect_NNParams(newModel, f'{resultsFolder}mc_iter_{iter+1}_params.dat', show=True)

            fig = plotPP(atomPPOrder, val_dataset.q, val_dataset.q, val_dataset.vq_atoms, currModel(val_dataset.q), "ZungerForm", f"mc_iter_{iter+1}", ["-",":" ]*len(atomPPOrder), True, NNConfig['SHOWPLOTS']);
            fig.savefig(f'{resultsFolder}mc_iter_{iter+1}_plotPP.png')
            torch.save(currModel.state_dict(), f'{resultsFolder}mc_iter_{iter+1}_PPmodel.pth')
        elif mc_accept_bool:   # new loss is higher, but we still accept.
            currLoss = newLoss
            currModel = newModel
            file_trainCost.write(f"{iter+1}    {newLoss.item()}    {1}    {currLoss.item()}\n")
            print(f"Accepted. currLoss={currLoss.item():.4f}")

            fig = plotPP(atomPPOrder, val_dataset.q, val_dataset.q, val_dataset.vq_atoms, currModel(val_dataset.q), "ZungerForm", f"mc_iter_{iter+1}", ["-",":" ]*len(atomPPOrder), True, NNConfig['SHOWPLOTS']);
            fig.savefig(f'{resultsFolder}mc_iter_{iter+1}_plotPP.png')
            torch.save(currModel.state_dict(), f'{resultsFolder}mc_iter_{iter+1}_PPmodel.pth')
        else:   # don't accept
            file_trainCost.write(f"{iter+1}    {newLoss.item()}    {0}    {currLoss.item()}\n")
            print(f"Not accepted. currLoss={currLoss.item():.4f}")
        
        trial_COST.append(newLoss.item())
        accepted_COST.append(currLoss.item())
    
        plt.close('all')
        torch.cuda.empty_cache()

    model = currModel
        
    fig_cost = plot_mc_cost(trial_COST, accepted_COST, True, NNConfig['SHOWPLOTS']);
    fig_cost.savefig(resultsFolder + 'final_mc_cost.png')
    file_trainCost.close()
    return (trial_COST, accepted_COST)