import math
import os

import numpy as np
import torch
import torch.nn as nn
from torch.optim import Adam

from metrics import accuracy
from utils import normalize_cifar10


def train_adv(model, train_loader, test_loader, attack, args, device='cpu'):
    optimizer = Adam(model.parameters(), lr=args['lr'])
    loss_func = nn.CrossEntropyLoss()
    if args['dataset'] == 'cifar10':
        norm_func = normalize_cifar10
    else:
        norm_func = None
    noise_entropy_threshold = math.log(args['var_threshold']) + (1 + math.log(2 * math.pi)) / 2
    best_test_acc = -1.
    train_accuracy = []
    test_accuracy = []
    for epoch in range(args['num_epochs']):
        for data, target in train_loader:
            data = data.to(device)
            target = target.to(device)
            # Apply the attack to generate perturbed data.
            if isinstance(args['epsilon'], float):
                perturbed_data = attack(model, data, target, epsilon=args['epsilon']).to(device)
            elif args['epsilon'] == 'rand':
                rand_epsilon = np.random.choice([8. / 255., 16. / 255., 32. / 255., 64. / 255., 128. / 255.])
                perturbed_data = attack(model, data, target, epsilon=rand_epsilon).to(device)
            else:
                perturbed_data = attack(model, data, target).to(device)
            model.train()
            # Compute logits for clean and perturbed data.
            if norm_func is not None:
                data = norm_func(data)
                perturbed_data = norm_func(perturbed_data)
            logits_clean = model(data)
            logits_adv = model(perturbed_data)
            optimizer.zero_grad()
            # Compute the cross-entropy loss for these logits.
            clean_loss = loss_func(logits_clean, target)
            adv_loss = loss_func(logits_adv, target)
            noise_entropy = torch.relu(noise_entropy_threshold - model.noise.dist.entropy()).mean()
            # Balance these two losses with weight w, and add the regularization term.
            w = args['adv_loss_w']
            loss = w * adv_loss + (1. - w) * clean_loss + args['reg_term'] * noise_entropy
            loss.backward()
            optimizer.step()
        train_accuracy.append(accuracy(model, train_loader, device=device, norm=norm_func))
        test_accuracy.append(accuracy(model, test_loader, device=device, norm=norm_func))
        # Checkpoint current model.
        torch.save(model.state_dict(), os.path.join(args['output_path']['models'], 'ckpt.pt'))
        # Save model with best testing performance.
        if test_accuracy[-1] > best_test_acc:
            best_test_acc = test_accuracy[-1]
            torch.save(model.state_dict(), os.path.join(args['output_path']['models'], 'ckpt_best.pt'))
        print('Epoch {}\t\tTrain acc: {:.3f}, Test acc: {:.3f}'.format(
            epoch + 1, train_accuracy[-1], test_accuracy[-1]))
    # Also save the training and testing curves.
    np.save(os.path.join(args['output_path']['stats'], 'train_acc.npy'), np.array(train_accuracy))
    np.save(os.path.join(args['output_path']['stats'], 'test_acc.npy'), np.array(test_accuracy))


def meta_train_adv(model, train_loader, val_loader, test_loader, attack, args, device='cpu'):
    optim_inner = Adam([
        {'params': model.gen.parameters()},
        {'params': model.noise.fc_mu.parameters()},
        {'params': model.noise.fc_sigma.parameters()},
        {'params': model.proto.parameters()},
    ], lr=args['lr'])
    optim_outer = Adam([
        {'params': model.noise.fc_b.parameters()},
        {'params': model.reg_term.parameters()},
    ], lr=args['meta_lr'])
    loss_func = nn.CrossEntropyLoss()
    if args['dataset'] == 'cifar10':
        norm_func = normalize_cifar10
    else:
        norm_func = None
    noise_entropy_threshold = math.log(args['var_threshold']) + (1 + math.log(2 * math.pi)) / 2
    best_test_acc = -1.
    train_accuracy = []
    test_accuracy = []
    b_hist, reg_term_hist = [], []
    val_iter = iter(val_loader)
    for epoch in range(args['num_epochs']):
        for data_inner, target_inner in train_loader:
            data_inner = data_inner.to(device)
            target_inner = target_inner.to(device)
            # Apply the attack to generate perturbed data.
            if isinstance(args['epsilon'], float):
                perturbed_data_inner = attack(model, data_inner, target_inner, epsilon=args['epsilon']).to(device)
            elif args['epsilon'] == 'rand':
                rand_epsilon = np.random.choice([8. / 255., 16. / 255., 32. / 255., 64. / 255., 128. / 255.])
                perturbed_data_inner = attack(model, data_inner, target_inner, epsilon=rand_epsilon).to(device)
            else:
                perturbed_data_inner = attack(model, data_inner, target_inner).to(device)
            model.train()
            # Compute logits for clean and perturbed data.
            if norm_func is not None:
                data_inner = norm_func(data_inner)
                perturbed_data_inner = norm_func(perturbed_data_inner)
            logits_clean_i = model(data_inner)
            logits_adv_i = model(perturbed_data_inner)
            optim_inner.zero_grad()
            # Compute the cross-entropy loss for these logits.
            clean_loss_i = loss_func(logits_clean_i, target_inner)
            adv_loss_i = loss_func(logits_adv_i, target_inner)
            noise_entropy_i = torch.relu(noise_entropy_threshold - model.noise.dist.entropy()).mean()
            # Balance these two losses with weight w, and add the regularization term.
            w = args['adv_loss_w']
            loss_inner = w * adv_loss_i + (1. - w) * clean_loss_i + model.get_reg_term() * noise_entropy_i
            loss_inner.backward()
            optim_inner.step()
            try:
                data_outer, target_outer = next(val_iter)
            except StopIteration:
                val_iter = iter(val_loader)
                data_outer, target_outer = next(val_iter)
            data_outer = data_outer.to(device)
            target_outer = target_outer.to(device)
            # Apply the attack to generate perturbed data.
            if isinstance(args['epsilon'], float):
                perturbed_data_outer = attack(model, data_outer, target_outer, epsilon=args['epsilon']).to(device)
            elif args['epsilon'] == 'rand':
                rand_epsilon = np.random.choice([8. / 255., 16. / 255., 32. / 255., 64. / 255., 128. / 255.])
                perturbed_data_outer = attack(model, data_outer, target_outer, epsilon=rand_epsilon).to(device)
            else:
                perturbed_data_outer = attack(model, data_outer, target_outer).to(device)
            model.train()
            # Compute logits for clean and perturbed data.
            if norm_func is not None:
                data_outer = norm_func(data_outer)
                perturbed_data_outer = norm_func(perturbed_data_outer)
            logits_clean_o = model(data_outer)
            logits_adv_o = model(perturbed_data_outer)
            optim_outer.zero_grad()
            # Compute the cross-entropy loss for these logits.
            clean_loss_outer = loss_func(logits_clean_o, target_outer)
            adv_loss_outer = loss_func(logits_adv_o, target_outer)
            noise_entropy_o = torch.relu(noise_entropy_threshold - model.noise.dist.entropy()).mean()
            # Balance these two losses with weight w, and add the regularization term.
            w = args['adv_loss_w']
            loss_outer = w * adv_loss_outer + (1. - w) * clean_loss_outer + model.get_reg_term() * noise_entropy_o
            loss_outer.backward()
            optim_outer.step()
        train_accuracy.append(accuracy(model, train_loader, device=device, norm=norm_func))
        test_accuracy.append(accuracy(model, test_loader, device=device, norm=norm_func))
        b_hist.append(model.get_b_vector().detach().numpy())
        reg_term_hist.append(model.get_reg_term().detach().numpy())
        # Checkpoint current model.
        torch.save(model.state_dict(), os.path.join(args['output_path']['models'], 'ckpt.pt'))
        # Save model with best testing performance.
        if test_accuracy[-1] > best_test_acc:
            best_test_acc = test_accuracy[-1]
            torch.save(model.state_dict(), os.path.join(args['output_path']['models'], 'ckpt_best.pt'))
        print('Epoch {}\t\tTrain acc: {:.3f}, Test acc: {:.3f}'.format(
            epoch + 1, train_accuracy[-1], test_accuracy[-1]))
    # Also save the training and testing curves.
    np.save(os.path.join(args['output_path']['stats'], 'train_acc.npy'), np.array(train_accuracy))
    np.save(os.path.join(args['output_path']['stats'], 'test_acc.npy'), np.array(test_accuracy))
    np.save(os.path.join(args['output_path']['stats'], 'b_hist.npy'), np.array(b_hist))
    np.save(os.path.join(args['output_path']['stats'], 'reg_term_hist.npy'), np.array(reg_term_hist))
