import os
import sys
sys.path.insert(1, os.path.join(sys.path[0], '../utils'))
import numpy as np
import argparse
import h5py
import math
import time
import logging
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from utilities import (create_folder, get_filename, 
    get_relative_path_no_extension, create_logging, load_scalar)
from data_generator import DataGenerator
from models import Cnn_9layers
from losses import clipwise_binary_crossentropy, framewise_binary_crossentropy
from evaluate import Evaluator
from pytorch_utils import move_data_to_gpu
import config


def train(args):
    '''Training. Model will be saved after several iterations. 
    
    Args: 
      dataset_dir: string, directory of dataset
      workspace: string, directory of workspace
      data_type: 'train_weak' | 'train_synthetic'
      model_name: string, e.g. 'Cnn_9layers'
      loss_type: string, e.g. 'clipwise_binary_crossentropy'
      batch_size: int
      cuda: bool
      mini_data: bool, set True for debugging on a small part of data
    '''

    # Arugments & parameters
    dataset_dir = args.dataset_dir
    workspace = args.workspace
    data_type = args.data_type
    model_name = args.model_name
    loss_type = args.loss_type
    batch_size = args.batch_size
    cuda = args.cuda and torch.cuda.is_available()
    mini_data = args.mini_data
    filename = args.filename
    
    mel_bins = config.mel_bins
    frames_per_second = config.frames_per_second
    classes_num = config.classes_num
    max_iteration = 10      # Number of mini-batches to evaluate on training data
    reduce_lr = True       
    
    # Paths
    if mini_data:
        prefix = 'minidata_'
    else:
        prefix = ''
        
    if loss_type in ['clipwise_binary_crossentropy']:
        strong_target_training = False
    elif loss_type in ['framewise_binary_crossentropy']:
        strong_target_training = True
    else:
        raise Exception('Incorrect argument!')
        
    train_relative_name = get_relative_path_no_extension(data_type)
    validate_relative_name = get_relative_path_no_extension('validation')
        
    train_hdf5_path = os.path.join(workspace, 'features', 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}.h5'.format(train_relative_name))
        
    validate_hdf5_path = os.path.join(workspace, 'features', 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}.h5'.format(validate_relative_name))
    
    scalar_path = os.path.join(workspace, 'scalars', 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        'train/weak.h5')
        
    train_metadata_path = os.path.join(dataset_dir, 'metadata', 
        '{}.csv'.format(train_relative_name))
        
    validate_metadata_path = os.path.join(dataset_dir, 'metadata', 'validation', 
        '{}.csv'.format(validate_relative_name))
        
    models_dir = os.path.join(workspace, 'models', filename, 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}'.format(train_relative_name), 'loss_type={}'.format(loss_type))
    create_folder(models_dir)
    
    temp_submission_path = os.path.join(workspace, '_temp', 'submissions', filename, 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}'.format(train_relative_name), 'loss_type={}'.format(loss_type), 
        '_submission.csv')
    create_folder(os.path.dirname(temp_submission_path))

    logs_dir = os.path.join(args.workspace, 'logs', filename, args.mode, 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}'.format(train_relative_name), 'loss_type={}'.format(loss_type))
    create_logging(logs_dir, filemode='w')
    logging.info(args)
        
    # Load scalar
    scalar = load_scalar(scalar_path)
    
    # Model
    Model = eval(model_name)
    model = Model(classes_num, strong_target_training)
    
    if cuda:
        model.cuda()
        
    loss_func = eval(loss_type)
    
    # Optimizer
    optimizer = optim.Adam(model.parameters(), lr=1e-3, betas=(0.9, 0.999),
        eps=1e-08, weight_decay=0.)

    # Data generator
    data_generator = DataGenerator(
        train_hdf5_path=train_hdf5_path, 
        validate_hdf5_path=validate_hdf5_path, 
        scalar=scalar, 
        batch_size=batch_size)
    
    # Evaluator
    evaluator = Evaluator(
        model=model, 
        data_generator=data_generator, 
        cuda=cuda)
    
    train_bgn_time = time.time()
    iteration = 0

    # Train on mini batches
    for batch_data_dict in data_generator.generate_train():
        
        # Evaluate
        if iteration % 200 == 0:

            logging.info('------------------------------------')
            logging.info('Iteration: {}'.format(iteration))

            train_fin_time = time.time()

            '''
            # Uncomment for evaluate on training data
            evaluator.evaluate(
                data_type='train', 
                metadata_path=train_metadata_path, 
                submission_path=temp_submission_path, 
                max_iteration=None)
            '''
            evaluator.evaluate(
                data_type='validate', 
                metadata_path=validate_metadata_path, 
                submission_path=temp_submission_path, 
                max_iteration=None)

            train_time = train_fin_time - train_bgn_time
            validate_time = time.time() - train_fin_time

            logging.info(
                'Train time: {:.3f} s, validate time: {:.3f} s'
                ''.format(train_time, validate_time))

            train_bgn_time = time.time()

        # Save model
        if iteration % 1000 == 0 and iteration > 0:
            checkpoint = {
                'iteration': iteration, 
                'model': model.state_dict(), 
                'optimizer': optimizer.state_dict()}

            checkpoint_path = os.path.join(
                models_dir, 'md_{}_iters.pth'.format(iteration))
                
            torch.save(checkpoint, checkpoint_path)
            logging.info('Model saved to {}'.format(checkpoint_path))
            
        # Reduce learning rate
        if reduce_lr and iteration % 200 == 0 and iteration > 0:
            for param_group in optimizer.param_groups:
                param_group['lr'] *= 0.9
        
        # Move data to GPU
        for key in batch_data_dict.keys():
            if key in ['feature', 'weak_target', 'strong_target']:
                batch_data_dict[key] = move_data_to_gpu(batch_data_dict[key], cuda)
        
        # Train
        model.train()
        batch_output_dict = model(batch_data_dict['feature'])
        
        # loss
        loss = loss_func(batch_output_dict, batch_data_dict)

        # Backward
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # Stop learning
        if iteration == 5000:
            break
            
        iteration += 1
        

def inference_validation(args):
    '''Training. Model will be saved after several iterations. 
    
    Args: 
      dataset_dir: string, directory of dataset
      workspace: string, directory of workspace
      data_type: 'train_weak' | 'train_synthetic'
      model_name: string, e.g. 'Cnn_9layers'
      loss_type: string, e.g. 'clipwise_binary_crossentropy'
      batch_size: int
      cuda: bool
      visualize: bool
      mini_data: bool, set True for debugging on a small part of data
    '''
    # Arugments & parameters
    dataset_dir = args.dataset_dir
    workspace = args.workspace
    data_type = args.data_type
    model_name = args.model_name
    loss_type = args.loss_type
    iteration = args.iteration
    batch_size = args.batch_size
    cuda = args.cuda and torch.cuda.is_available()
    visualize = args.visualize
    mini_data = args.mini_data
    filename = args.filename
    
    mel_bins = config.mel_bins
    frames_per_second = config.frames_per_second
    classes_num = config.classes_num
    
    # Paths
    if mini_data:
        prefix = 'minidata_'
    else:
        prefix = ''
        
    if loss_type in ['clipwise_binary_crossentropy']:
        strong_target_training = False
    elif loss_type in ['framewise_binary_crossentropy']:
        strong_target_training = True
    else:
        raise Exception('Incorrect argument!')
        
    train_relative_name = get_relative_path_no_extension(data_type)
    validate_relative_name = get_relative_path_no_extension('validation')
    
    validate_metadata_path = os.path.join(dataset_dir, 'metadata', 'validation', 
        '{}.csv'.format(validate_relative_name))
    
    train_hdf5_path = os.path.join(workspace, 'features', 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}.h5'.format(train_relative_name))
    
    validate_hdf5_path = os.path.join(workspace, 'features', 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}.h5'.format(validate_relative_name))
    
    scalar_path = os.path.join(workspace, 'scalars', 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        'train/weak.h5')
        
    checkoutpoint_path = os.path.join(workspace, 'models', filename, 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}'.format(train_relative_name), 'loss_type={}'.format(loss_type), 'md_{}_iters.pth'.format(iteration))

    submission_path = os.path.join(workspace, 'submissions', filename, 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}'.format(train_relative_name), 'loss_type={}'.format(loss_type), '{}_iterations'.format(iteration), 'validation_submission.csv')
    create_folder(os.path.dirname(submission_path))

    logs_dir = os.path.join(args.workspace, 'logs', filename, args.mode, 
        '{}logmel_{}frames_{}melbins'.format(prefix, frames_per_second, mel_bins), 
        '{}'.format(train_relative_name), 'loss_type={}'.format(loss_type))
    create_logging(logs_dir, filemode='w')
    logging.info(args)
        
    # Load scalar
    scalar = load_scalar(scalar_path)

    # Load model
    Model = eval(model_name)
    model = Model(classes_num, strong_target_training)
    checkpoint = torch.load(checkoutpoint_path)
    model.load_state_dict(checkpoint['model'])
    
    if cuda:
        model.cuda()
        
    # Data generator
    data_generator = DataGenerator(
        train_hdf5_path=train_hdf5_path, 
        validate_hdf5_path=validate_hdf5_path, 
        scalar=scalar, 
        batch_size=batch_size)
        
    # Evaluator
    evaluator = Evaluator(
        model=model, 
        data_generator=data_generator, 
        cuda=cuda)

    evaluator.evaluate(
        data_type='validate', 
        metadata_path=validate_metadata_path, 
        submission_path=submission_path)
    
    if visualize:
        '''
        # Uncomment for visualize prediction on training data
        evaluator.visualize(data_type='train')
        '''
        evaluator.visualize(data_type='validate')
        

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Example of parser. ')
    subparsers = parser.add_subparsers(dest='mode')

    parser_train = subparsers.add_parser('train')
    parser_train.add_argument('--dataset_dir', type=str, required=True)
    parser_train.add_argument('--workspace', type=str, required=True)
    parser_train.add_argument('--data_type', type=str, required=True, 
        choices=['train_weak', 'train_synthetic'])
    parser_train.add_argument('--model_name', type=str, required=True)
    parser_train.add_argument('--loss_type', type=str, required=True)
    parser_train.add_argument('--batch_size', type=int, required=True)
    parser_train.add_argument('--cuda', action='store_true', default=False)
    parser_train.add_argument('--mini_data', action='store_true', default=False)
    
    parser_inference_validation = subparsers.add_parser('inference_validation')
    parser_inference_validation.add_argument('--dataset_dir', type=str, required=True)
    parser_inference_validation.add_argument('--workspace', type=str, required=True)
    parser_inference_validation.add_argument('--data_type', type=str, required=True)
    parser_inference_validation.add_argument('--model_name', type=str, required=True)
    parser_inference_validation.add_argument('--loss_type', type=str, required=True)
    parser_inference_validation.add_argument('--iteration', type=int, required=True)
    parser_inference_validation.add_argument('--batch_size', type=int, required=True)
    parser_inference_validation.add_argument('--cuda', action='store_true', default=False)
    parser_inference_validation.add_argument('--visualize', action='store_true', default=False)
    parser_inference_validation.add_argument('--mini_data', action='store_true', default=False)
    
    args = parser.parse_args()
    args.filename = get_filename(__file__)

    if args.mode == 'train':
        train(args)

    elif args.mode == 'inference_validation':
        inference_validation(args)

    else:
        raise Exception('Error argument!')