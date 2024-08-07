import torch
import numpy as np
from itertools import product
import matplotlib as mpl
import matplotlib.pyplot as plt 
mpl.rcParams['lines.markersize'] = 3
from .constants import * 

torch.set_default_dtype(torch.float64)

def pot_func(x, params): 
    pot = (params[0]*(x*x - params[1]) / (params[2] * torch.exp(params[3]*x*x) - 1.0))
    return pot


def pot_funcLR(x, params, gamma):
    pot = params[0]*(x*x - params[1]) / (params[2] * torch.exp(params[3]*x*x) - 1.0)
    nzid = torch.nonzero(x > 1e-4, as_tuple=True) # x is batched, but want to avoid division by 0
    pot[nzid] -= params[4] * 4 * np.pi / (x[nzid]**2) * torch.exp(-1 * x[nzid]**2 / (4*gamma**2))
    return pot
  

def realSpacePot(vq, qSpacePot, nRGrid, rmax=25): 
    # vq and qSpacePot are both 1D tensor of torch.Size([nQGrid]). vq is assumed to be equally spaced. 
    # rmax and nRGrid are both scalars
    dq = vq[1] - vq[0]
    
    # dr = 0.02*2*np.pi / (nGrid * dq)
    # vr = torch.linspace(0, (nGrid - 1) * dr, nGrid)
    vr = torch.linspace(0, rmax, nRGrid)
    rSpacePot = torch.zeros(nRGrid)
    
    for ir in range(nRGrid): 
        if ir==0: 
            prefactor = 4*np.pi*dq / (8*np.pi**3)
            rSpacePot[ir] = torch.sum(prefactor * vq**2 * qSpacePot)
        else: 
            prefactor = 4*np.pi*dq / (8*np.pi**3 * vr[ir])
            rSpacePot[ir] = torch.sum(prefactor * vq * torch.sin(vq * vr[ir]) * qSpacePot)

    return (vr.view(-1,1), rSpacePot.view(-1,1))


def plotBandStruct(bulkSystem_list, bandStruct_list, SHOWPLOTS): 
    # The input bandStruct_list is a list of tensors. They should be ordered as: 
    # ref_system1, predict_system1, ref_system2, predict_system2, ..., ref_systemN, predict_systemN
    systemNames = [x.systemName for x in bulkSystem_list]
    nSystem = len(systemNames)
    if (len(bandStruct_list)!=2*nSystem): 
        raise ValueError("The lengths of bandStruct_list do not match the expected values.")

    fig, axs = plt.subplots(nSystem, 2, figsize=(9, 4 * nSystem))
    axs_flat = axs.flatten()
    for iSystem in range(nSystem): 
        # plot ref
        numBands = len(bandStruct_list[2*iSystem][0])
        numKpts = len(bandStruct_list[2*iSystem])
        for i in range(numBands): 
            if i==0: 
                axs_flat[2*iSystem+0].plot(np.arange(numKpts), bandStruct_list[2*iSystem][:, i].detach().numpy(), "bo", alpha=0.5, markersize=2, label="Reference")
                axs_flat[2*iSystem+1].plot(np.arange(numKpts), bandStruct_list[2*iSystem][:, i].detach().numpy(), "bo", alpha=0.5, markersize=2, label="Reference")
            else: 
                axs_flat[2*iSystem+0].plot(np.arange(numKpts), bandStruct_list[2*iSystem][:, i].detach().numpy(), "bo", alpha=0.5, markersize=2)
                axs_flat[2*iSystem+1].plot(np.arange(numKpts), bandStruct_list[2*iSystem][:, i].detach().numpy(), "bo", alpha=0.5, markersize=2)
                
        # plot prediction
        numBands = len(bandStruct_list[2*iSystem+1][0])
        numKpts = len(bandStruct_list[2*iSystem+1])
        for i in range(numBands): 
            if i==0: 
                axs_flat[2*iSystem+0].plot(np.arange(numKpts), bandStruct_list[2*iSystem+1][:, i].detach().numpy(), "r-", alpha=0.6, label="NN prediction")
                axs_flat[2*iSystem+1].plot(np.arange(numKpts), bandStruct_list[2*iSystem+1][:, i].detach().numpy(), "r-", alpha=0.6, label="NN prediction")
            else: 
                axs_flat[2*iSystem+0].plot(np.arange(numKpts), bandStruct_list[2*iSystem+1][:, i].detach().numpy(), "r-", alpha=0.6)
                axs_flat[2*iSystem+1].plot(np.arange(numKpts), bandStruct_list[2*iSystem+1][:, i].detach().numpy(), "r-", alpha=0.6)
        axs_flat[2*iSystem+0].legend(frameon=False)
        # refEList = bandStruct_list[2*iSystem][bandStruct_list[2*iSystem] > -50]
        # refEmin = torch.min(refEList).item()
        # refEmax = torch.max(refEList).item()
        # predEList = bandStruct_list[2*iSystem+1][bandStruct_list[2*iSystem+1] > -50]
        # predEmin = torch.min(predEList).item()
        # predEmax = torch.max(predEList).item()
        # axs_flat[2*iSystem+0].set(ylim=(min(refEmin, predEmin)-0.5, max(refEmax, predEmax)+0.5))
        axs_flat[2*iSystem+0].set(ylim=(bulkSystem_list[iSystem].BS_plot_center-bulkSystem_list[iSystem].BS_plot_CBVB_range, bulkSystem_list[iSystem].BS_plot_center+bulkSystem_list[iSystem].BS_plot_CBVB_range))
        axs_flat[2*iSystem+1].set(ylim=(bulkSystem_list[iSystem].BS_plot_center-bulkSystem_list[iSystem].BS_plot_CBVB_range_zoom, bulkSystem_list[iSystem].BS_plot_center+bulkSystem_list[iSystem].BS_plot_CBVB_range_zoom), title=systemNames[iSystem])
        # axs_flat[2*iSystem+0].get_xaxis().set_ticks([0, 20, 40, 45, 60])
        # axs_flat[2*iSystem+0].get_xaxis().set_ticklabels(["L", r"$\Gamma$", "X", "K", r"$\Gamma$"])
        # axs_flat[2*iSystem+1].get_xaxis().set_ticks([0, 20, 40, 45, 60])
        # axs_flat[2*iSystem+1].get_xaxis().set_ticklabels(["L", r"$\Gamma$", "X", "K", r"$\Gamma$"])

    fig.tight_layout()
    if SHOWPLOTS: 
        plt.show()
    return fig


def plotBandStructFromFile(refFile, calcFile): 
    refBS = np.loadtxt(refFile)[:, 1:]
    calcBS = np.loadtxt(calcFile)[:, 1:]

    fig, axs = plt.subplots(1, 2, figsize=(9, 4))
    # plot ref
    numBands = len(refBS[0])
    numKpts = len(refBS)
    for i in range(numBands): 
        if i==0: 
            axs[0].plot(np.arange(numKpts), refBS[:, i], "bo", alpha=0.5, markersize=2, label="Reference")
            axs[1].plot(np.arange(numKpts), refBS[:, i], "bo", alpha=0.5, markersize=2, label="Reference")
        else: 
            axs[0].plot(np.arange(numKpts), refBS[:, i], "bo", alpha=0.5, markersize=2)
            axs[1].plot(np.arange(numKpts), refBS[:, i], "bo", alpha=0.5, markersize=2)

    # plot prediction
    numBands = len(calcBS[0])
    numKpts = len(calcBS)
    for i in range(numBands): 
        if i==0: 
            axs[0].plot(np.arange(numKpts), calcBS[:, i], "r-", alpha=0.6, label="Calc")
            axs[1].plot(np.arange(numKpts), calcBS[:, i], "r-", alpha=0.6, label="Calc")
        else: 
            axs[0].plot(np.arange(numKpts), calcBS[:, i], "r-", alpha=0.6)
            axs[1].plot(np.arange(numKpts), calcBS[:, i], "r-", alpha=0.6)
    axs[0].legend(frameon=False)
    axs[0].set(ylim=(-3000, -1000))
    axs[1].set(ylim=(-9.5, -1.5))

    fig.tight_layout()
    return fig


def plotBandStruct_reorder(newOrderBS, bandIdx): 
    fig, ax = plt.subplots(1, 1, figsize=(8,8))

    numBands = len(newOrderBS[0])
    numKpts = len(newOrderBS)
    for i in range(numBands): 
        if i==0: 
            ax.plot(np.arange(numKpts), newOrderBS[:, i], "bo-", alpha=0.1, markersize=2)
        else: 
            ax.plot(np.arange(numKpts), newOrderBS[:, i], "bo-", alpha=0.1, markersize=2)

    # plot new ordering
    numKpts = len(newOrderBS)
    ax.plot(np.arange(numKpts), newOrderBS[:, bandIdx], "ro-", alpha=0.8, markersize=2, label=f"band{bandIdx}")
    ax.legend()
    ax.set(ylim=(min(newOrderBS[:, bandIdx])-0.2, max(newOrderBS[:, bandIdx])+0.2))
    # ax.get_xaxis().set_ticks([0, 10, 20, 30, 40, 50, 60, 70, 79, 80, 90, 100, 108, 110, 120, 130, 140, 149])
    # ax.get_xaxis().set_ticklabels(["R", 10, 20, 30, 40, r"$\Gamma$", 60, 70, "X", 80, 90, 100, "M", 110, 120, 130, 140, r"$\Gamma$"])
    ax.grid(alpha=0.5)

    fig.tight_layout()
    return fig, ax


def plotPP(atomPPOrder, ref_q, pred_q, ref_vq_atoms, pred_vq_atoms, ref_labelName, pred_labelName, lineshape_array, boolPlotDiff, SHOWPLOTS):
    # ref_vq_atoms and pred_vq_atoms are 2D tensors. Each tensor contains the pseudopotential (either ref or pred)
    # for atoms in the order of atomPPOrder. 
    # ref_labelName and pred_labelName are strings. 
    # lineshape_array has twice the length of atomPPOrder, with: ref_atom1, pred_atom1, ref_atom2, pred_atom2, ... 
    if boolPlotDiff and torch.equal(ref_q, pred_q): 
        fig, axs = plt.subplots(1,3, figsize=(12,4))
        ref_q = ref_q.view(-1).detach().numpy()
        pred_q = pred_q.view(-1).detach().numpy()
        
        for iAtom in range(len(atomPPOrder)):
            ref_vq = ref_vq_atoms[:, iAtom].view(-1).detach().numpy()
            pred_vq = pred_vq_atoms[:, iAtom].view(-1).detach().numpy()
            axs[0].plot(ref_q, ref_vq, lineshape_array[iAtom*2], label=atomPPOrder[iAtom]+" "+ref_labelName)
            axs[0].plot(pred_q, pred_vq, lineshape_array[iAtom*2+1], label=atomPPOrder[iAtom]+" "+pred_labelName)
            axs[1].plot(ref_q, pred_vq - ref_vq, lineshape_array[iAtom*2], label=atomPPOrder[iAtom]+" diff (pred - ref)")
            (ref_vr, ref_rSpacePot) = realSpacePot(torch.tensor(ref_q), torch.tensor(ref_vq), 3000)
            (pred_vr, pred_rSpacePot) = realSpacePot(torch.tensor(pred_q), torch.tensor(pred_vq), 3000)
            axs[2].plot(ref_vr.view(-1).detach().numpy(), ref_rSpacePot.view(-1).detach().numpy(), lineshape_array[iAtom*2], label=atomPPOrder[iAtom]+" "+ref_labelName)
            axs[2].plot(pred_vr.view(-1).detach().numpy(), pred_rSpacePot.view(-1).detach().numpy(), lineshape_array[iAtom*2+1], label=atomPPOrder[iAtom]+" "+pred_labelName)
        axs[0].set(xlabel=r"$q$", ylabel=r"$v(q)$", xlim=(0,9))
        axs[0].legend(frameon=False)
        axs[1].set(xlabel=r"$q$", ylabel=r"$v_{NN}(q) - v_{func}(q)$", xlim=(0,9))
        axs[1].legend(frameon=False)
        axs[2].set(xlabel=r"$r$", ylabel=r"$v(r)$", xlim=(0,12))
        axs[2].legend(frameon=False)
    
    else:
        fig, axs = plt.subplots(1,2, figsize=(9,4))
        ref_q = ref_q.view(-1).detach().numpy()
        pred_q = pred_q.view(-1).detach().numpy()
        
        for iAtom in range(len(atomPPOrder)):
            ref_vq = ref_vq_atoms[:, iAtom].view(-1).detach().numpy()
            pred_vq = pred_vq_atoms[:, iAtom].view(-1).detach().numpy()
            axs[0].plot(ref_q, ref_vq, lineshape_array[iAtom*2], label=atomPPOrder[iAtom]+" "+ref_labelName)
            axs[0].plot(pred_q, pred_vq, lineshape_array[iAtom*2+1], label=atomPPOrder[iAtom]+" "+pred_labelName)
            (ref_vr, ref_rSpacePot) = realSpacePot(torch.tensor(ref_q), torch.tensor(ref_vq), 3000)
            (pred_vr, pred_rSpacePot) = realSpacePot(torch.tensor(pred_q), torch.tensor(pred_vq), 3000)
            axs[1].plot(ref_vr.view(-1).detach().numpy(), ref_rSpacePot.view(-1).detach().numpy(), lineshape_array[iAtom*2], label=atomPPOrder[iAtom]+" "+ref_labelName)
            axs[1].plot(pred_vr.view(-1).detach().numpy(), pred_rSpacePot.view(-1).detach().numpy(), lineshape_array[iAtom*2+1], label=atomPPOrder[iAtom]+" "+pred_labelName)
        axs[0].set(xlabel=r"$q$", ylabel=r"$v(q)$", xlim=(0,9))
        axs[0].legend(frameon=False)
        axs[1].set(xlabel=r"$r$", ylabel=r"$v(r)$", xlim=(0,12))
        axs[1].legend(frameon=False)
        
    fig.tight_layout()
    if SHOWPLOTS: 
        plt.show()
    return fig


def plot_training_validation_cost(training_cost_x, training_cost, validation_cost_x=None, validation_cost=None, ylogBoolean=True, SHOWPLOTS=False): 
    fig, axs = plt.subplots(1, 1, figsize=(6, 4))
    
    # epochs = range(0, len(training_cost))
    axs.plot(training_cost_x, training_cost, "b-", label='Training Cost')     # np.array(epochs)+1

    if (validation_cost_x is not None) and (validation_cost is not None) and (len(validation_cost) != 0): 
        # evaluation_frequency = len(training_cost) // len(validation_cost)
        # evaluation_epochs = list(range(evaluation_frequency-1, len(training_cost), evaluation_frequency))
        # axs.plot(np.array(evaluation_epochs)+1, validation_cost, "r:", label='Validation Cost')
        axs.plot(validation_cost_x, validation_cost, "r:", label='Validation Cost')

    if ylogBoolean:
        axs.set_yscale('log')
    else:
        axs.set_yscale('linear')
    axs.set(xlabel="Epochs", ylabel="Cost", title="Training and Validation Costs")
    axs.legend(frameon=False)
    axs.grid(True)
    fig.tight_layout()
    if SHOWPLOTS:
        plt.show()
    return fig


def FT_converge_and_write_pp(atomPPOrder, qmax_array, nQGrid_array, nRGrid_array, model, val_dataset, xmin, xmax, ymin, ymax, choiceQMax, choiceNQGrid, choiceNRGrid, ppPlotFilePrefix, potRAtomFilePrefix, SHOWPLOTS):
    cmap = plt.get_cmap('rainbow')
    figtot, axstot = plt.subplots(1, len(atomPPOrder), figsize=(9,4))
    
    combinations = list(product(qmax_array, nQGrid_array, nRGrid_array))
    cmap = plt.get_cmap('rainbow')
    colors = cmap(np.linspace(0, 1, len(combinations)))
    for i, combo in enumerate(combinations):
        qmax, nQGrid, nRGrid = combo

        qGrid = torch.linspace(0.0, qmax, nQGrid).view(-1, 1)
        NN = model(qGrid)
        for iAtom in range(len(atomPPOrder)):
            (vr, rSpacePot) = realSpacePot(qGrid.view(-1), NN[:, iAtom].view(-1), nRGrid)
            if (qmax==choiceQMax) and (nQGrid==choiceNQGrid) and (nRGrid==choiceNRGrid): 
                axstot[iAtom].plot(vr.detach().numpy(), rSpacePot.detach().numpy(), "-", color=colors[i], label="My FT, 0<q<%d, nQGrid=%d, nRGrid=%d" % (qmax,nQGrid,nRGrid))
            else:
                axstot[iAtom].plot(vr.detach().numpy(), rSpacePot.detach().numpy(), "-", color=colors[i], label="0<q<%d, nQGrid=%d, nRGrid=%d" % (qmax,nQGrid,nRGrid))
    
    for iAtom in range(len(atomPPOrder)):
        axstot[iAtom].set(xlim=(xmin, xmax), ylim=(ymin, ymax), title=atomPPOrder[iAtom]+" PP", xlabel=r"$r$ (Bohr radius)", ylabel=r"$v(r)$")
    axstot[0].legend(frameon=False, fontsize=7)
    figtot.tight_layout()
    figtot.savefig(ppPlotFilePrefix+"converge.png") 
    if SHOWPLOTS: 
        plt.show()
    
    choiceQGrid = torch.linspace(0.0, choiceQMax, choiceNQGrid).view(-1, 1)
    NN = model(choiceQGrid)
    fig = plotPP(atomPPOrder, val_dataset.q, choiceQGrid, val_dataset.vq_atoms, NN, "ZungerForm", "NN", ["-",":" ]*len(atomPPOrder), False, SHOWPLOTS);
    fig.savefig(ppPlotFilePrefix+".png") 
    for iAtom in range(len(atomPPOrder)):
        (vr, rSpacePot) = realSpacePot(choiceQGrid.view(-1), NN[:, iAtom].view(-1), choiceNRGrid)
        pot = torch.cat((vr, rSpacePot), dim=1).detach().numpy()
        np.savetxt(potRAtomFilePrefix+"_"+atomPPOrder[iAtom]+".dat", pot, delimiter='    ', fmt='%e')
    if SHOWPLOTS: 
        plt.show()
    return


def plot_multiple_train_cost(*file_groups, labels=None, ylogBoolean=False, ymin=None, ymax=None, xlabel='nEpoch', ylabel='Training cost'):
    fig, ax = plt.subplots(1, 1, figsize=(6, 4))
    
    if labels is None:
        labels = [f'Group {i+1}' for i in range(len(file_groups))]
    
    for i, file_group in enumerate(file_groups):
        all_cost = np.zeros(0)
        for filename in file_group:
            data = np.loadtxt(filename)[:,1]
            all_cost = np.hstack([all_cost, data])
        ax.plot(all_cost, "-", alpha=0.7, label=labels[i])
    
    if ylogBoolean:
        ax.set_yscale('log')
    if (ymin is not None) or (ymax is not None):
        current_ylim = ax.get_ylim()
        new_ylim = (ymin if ymin is not None else current_ylim[0], ymax if ymax is not None else current_ylim[1])
        ax.set_ylim(new_ylim)

    ax.set(xlabel=xlabel, ylabel=ylabel)
    ax.legend()
    fig.tight_layout()
    return fig


def plot_mc_cost(trial_cost, accepted_cost, ylogBoolean, SHOWPLOTS): 
    fig, ax = plt.subplots(1, 1, figsize=(6, 4))
    
    iter = range(0, len(trial_cost))
    ax.plot(np.array(iter)+1, trial_cost, "b-", label='Trial Cost')
    ax.plot(np.array(iter)+1, accepted_cost, "r:", label='Accepted Cost')

    if ylogBoolean:
        ax.set_yscale('log')
    else:
        ax.set_yscale('linear')
    ax.set(xlabel="Iterations", ylabel="Cost", title="Trial and Accepted Costs")
    ax.legend(frameon=False)
    ax.grid(True)
    fig.tight_layout()
    if SHOWPLOTS:
        plt.show()
    return fig