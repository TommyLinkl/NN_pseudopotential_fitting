import os
import numpy as np
import time
import torch
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import ExponentialLR
import pathlib

from constants.constants import *
from utils.nn_models import Net_relu_xavier_decay2
from utils.read import BulkSystem, read_NNConfigFile, read_PPparams
from utils.pp_func import plotPP
from utils.init_NN_train import init_Zunger_data, init_Zunger_weighted_mse, init_Zunger_train_GPU
from utils.NN_train import weighted_mse_bandStruct, weighted_mse_energiesAtKpt, bandStruct_train_GPU
from utils.ham import Hamiltonian
from utils.memory import print_memory_usage, plot_memory_usage, set_debug_memory_flag

def run_memory_test(inputsFolder, resultsFolder, device): 
    NNConfig = read_NNConfigFile(inputsFolder + 'NN_config.par')
    SHOWPLOTS = NNConfig['SHOWPLOTS']  # True or False
    nSystem = NNConfig['nSystem']
    checkpoint = NNConfig['checkpoint']  # True or False
    separateKptGrad = NNConfig['separateKptGrad']  # True or False
    if (checkpoint==1) and (separateKptGrad==1): 
        raise ValueError("############################################\nPlease don't turn on both checkpoint and separateKptGrad. \n############################################\n")
    elif (checkpoint==1) and (separateKptGrad==0):
        print("############################################\nWARNING: Using checkpointing! Please use this as a last resort, only for pseudopotential fitting where memory limit is a major issue. The code will run slower due to checkpointing. \n############################################\n")
    elif (checkpoint==0) and (separateKptGrad==1): 
        print("############################################\nUsing separateKptGrad. This can decrease the peak memory load during the fitting code. \n############################################\n")

    # Read and set up systems
    print("############################################\nReading and setting up the BulkSystems. ")
    atomPPOrder = []
    systems = [BulkSystem() for _ in range(nSystem)]
    for iSys in range(nSystem): 
        systems[iSys].setSystem(inputsFolder + "system_%d.par" % iSys)
        systems[iSys].setInputs(inputsFolder + "input_%d.par" % iSys)
        systems[iSys].setKPointsAndWeights(inputsFolder + "kpoints_%d.par" % iSys)
        systems[iSys].setExpBS(inputsFolder + "expBandStruct_%d.par" % iSys)
        systems[iSys].setBandWeights(inputsFolder + "bandWeights_%d.par" % iSys)
        atomPPOrder.append(systems[iSys].atomTypes)

    # Calculate atomPPOrder. Read in initial PPparams. Set up NN accordingly
    atomPPOrder = np.unique(np.concatenate(atomPPOrder))
    nPseudopot = len(atomPPOrder)
    print("There are %d atomic pseudopotentials. They are in the order of: " % nPseudopot)
    print(atomPPOrder)
    allSystemNames = [x.systemName for x in systems]
    PPparams, totalParams = read_PPparams(atomPPOrder, inputsFolder + "init_")
    localPotParams = totalParams[:,:4]
    layers = [1] + NNConfig['hiddenLayers'] + [nPseudopot]
    PPmodel = Net_relu_xavier_decay2(layers)
    print_memory_usage()


    # Initialize the ham class for each BulkSystem
    print("Initializing the ham class for each BulkSystem. ")
    hams = []
    for iSys in range(nSystem): 
        start_time = time.time()
        # ham = Hamiltonian(systems[iSys], PPparams, atomPPOrder, device, SObool=True)
        ########################## Initializting the hamiltonian with SO takes 7.6GB of memory for this trial run. 
        ham = Hamiltonian(systems[iSys], PPparams, atomPPOrder, device, NNConfig, SObool=False)
        hams.append(ham)
        end_time = time.time()
        print(f"Finished initializing {iSys}-th Hamiltonian Class... Elapsed time: {(end_time - start_time):.2f} seconds")

    print_memory_usage()

    ############# Initialize the NN to the local pot function form #############
    train_dataset = init_Zunger_data(atomPPOrder, localPotParams, True)
    val_dataset = init_Zunger_data(atomPPOrder, localPotParams, False)

    if os.path.exists(inputsFolder + 'init_PPmodel.pth'):
        print(f"\n############################################\nInitializing the NN with file {inputsFolder}init_PPmodel.pth.")
        PPmodel.load_state_dict(torch.load(inputsFolder + 'init_PPmodel.pth'))
        print(f"\nDone with NN initialization to the file {inputsFolder}init_PPmodel.pth.")
    else:
        print("\n############################################\nInitializing the NN by fitting to the latest function form of pseudopotentials. ")
        PPmodel.cpu()
        PPmodel.eval()
        NN_init = PPmodel(val_dataset.q)
        plotPP(atomPPOrder, val_dataset.q, val_dataset.q, val_dataset.vq_atoms, NN_init, "ZungerForm", "NN_init", ["-",":" ]*nPseudopot, False, NNConfig['SHOWPLOTS'])

        init_Zunger_criterion = init_Zunger_weighted_mse
        init_Zunger_optimizer = torch.optim.Adam(PPmodel.parameters(), lr=NNConfig['init_Zunger_optimizer_lr'])
        init_Zunger_scheduler = ExponentialLR(init_Zunger_optimizer, gamma=NNConfig['init_Zunger_scheduler_gamma'])
        trainloader = DataLoader(dataset = train_dataset, batch_size = int(train_dataset.len/4),shuffle=True)
        validationloader = DataLoader(dataset = val_dataset, batch_size =val_dataset.len, shuffle=False)

        start_time = time.time()
        init_Zunger_train_GPU(PPmodel, device, trainloader, validationloader, init_Zunger_criterion, init_Zunger_optimizer, init_Zunger_scheduler, 20, NNConfig['init_Zunger_num_epochs'], NNConfig['init_Zunger_plotEvery'], atomPPOrder, NNConfig['SHOWPLOTS'], resultsFolder)
        end_time = time.time()
        elapsed_time = end_time - start_time
        print("GPU initialization: elapsed time: %.2f seconds" % elapsed_time)

        torch.save(PPmodel.state_dict(), resultsFolder + 'initZunger_PPmodel.pth')

        print("\nDone with NN initialization to the latest function form.")

    print_memory_usage()

    ############# Fit NN to band structures ############# 
    print("\n############################################\nStart training of the NN to fit to band structures. ")

    criterion_singleSystem = weighted_mse_bandStruct
    criterion_singleKpt = weighted_mse_energiesAtKpt
    optimizer = torch.optim.Adam(PPmodel.parameters(), lr=NNConfig['optimizer_lr'])
    scheduler = ExponentialLR(optimizer, gamma=NNConfig['scheduler_gamma'])

    start_time = time.time()
    (training_cost, validation_cost) = bandStruct_train_GPU(PPmodel, device, NNConfig, systems, hams, atomPPOrder, localPotParams, criterion_singleSystem, criterion_singleKpt, optimizer, scheduler, val_dataset, resultsFolder)
    end_time = time.time()
    elapsed_time = end_time - start_time
    print("GPU training: elapsed time: %.2f seconds" % elapsed_time)
    torch.cuda.empty_cache()

    plot_memory_usage(resultsFolder)
