import imageio
import numpy as np
import torch
import torch.nn as nn
from torchvision.utils import make_grid
from torch.autograd import Variable
from torch.autograd import grad as torch_grad
import time

class Trainer():
    def __init__(self, classifier, generator, discriminator, gen_optimizer, dis_optimizer, nb_g,
                 gp_weight=10, gamma=12.2, critic_iterations=5, print_every=50,
                 use_cuda=True):
        self.classifier = classifier
        self.G = generator
        self.G_opt = gen_optimizer
        self.D = discriminator
        self.D_opt = dis_optimizer
        self.nb_g = nb_g

        self.losses = {'G': [], 'D': [], 'GP': [], 'gradient_norm': []}
        for i in range(self.nb_g):
            self.losses['G_{}'.format(i+1)] = []
        self.num_steps = 0
        self.use_cuda = use_cuda
        self.gp_weight = gp_weight
        self.critic_iterations = critic_iterations
        self.print_every = print_every
        self.gamma = gamma
        if self.use_cuda:
            for g in self.G :
                g.cuda()
            self.D.cuda()

    def _critic_train_iteration(self, data):
        """   """
        # Get generated data
        batch_size = data.size()[0] // self.nb_g

        generated_data = []
        for i in range(self.nb_g):
            generated_data.append(self.sample_generator(batch_size, self.G[i]))
            # 32 , 1 , 32 , 32
            # 2, 32 , 1 , 32 , 32

            #=> 64, 1, 32, 32
        generated_data = torch.stack(generated_data).view(data.size()[0], 1, 32, 32)
        # Calculate probabilities on real and generated data
        data = Variable(data)
        if self.use_cuda:
            data = data.cuda()
        d_real = self.D(data)
        d_generated = self.D(generated_data)

        # Get gradient penalty
        gradient_penalty = self._gradient_penalty(data, generated_data)
        self.losses['GP'].append(gradient_penalty.item())

        # Create total loss and optimize
        self.D_opt.zero_grad()
        d_loss = d_generated.mean() - d_real.mean() + gradient_penalty
        d_loss.backward()

        self.D_opt.step()

        # Record loss
        self.losses['D'].append(d_loss.item())

    def _generator_train_iteration(self, data, gamma):
        """ """

        generated_data = []
        g_loss_list = []
        batch_size = data.size()[0]

        for i in range(self.nb_g):
            generated_data.append(self.sample_generator(batch_size, self.G[i]))

        

        for i in range(self.nb_g):
            
            self.G_opt[i].zero_grad()

            delta = 0.0 
            if self.gamma != 0:
                for z in range(self.nb_g) :
                    for j in range(z + 1 , self.nb_g) :
                        delta += self._tvd_loss(torch.nn.Softmax()(self.classifier.classifier(generated_data[z])) , torch.nn.Softmax()(self.classifier.classifier(generated_data[j])))
                delta /= self.nb_g

            # Get generated data
            generated_data_i = self.sample_generator(batch_size, self.G[i])
            d_generated_i = self.D(generated_data_i)

            #generated_data.append(generated_data_i)

            g_loss = - d_generated_i.mean() # + (self.gamma * (1 - delta)).mean()
            

            g_loss_list.append(g_loss)

            self.losses['G_{}'.format(i+1)].append(g_loss.cpu().detach().numpy())
            #print('G_{}'.format(i+1),self.losses['G_{}'.format(i+1)])
            # print("gloss : ",g_loss.shape)
            g_loss.backward()
            self.G_opt[i].step()

        # Record loss
        self.losses['G'].append(torch.Tensor(g_loss_list).mean().item())

    def _tvd_loss(self,P , Q):
        return 0.5 * (P - Q).abs().sum(axis = 1) 

    def _gradient_penalty(self, real_data, generated_data):
        batch_size = real_data.size()[0]

        # Calculate interpolation
        alpha = torch.rand(batch_size, 1, 1, 1)
        alpha = alpha.expand_as(real_data)
        if self.use_cuda:
            alpha = alpha.cuda()
        interpolated = alpha * real_data.data + (1 - alpha) * generated_data.data
        interpolated = Variable(interpolated, requires_grad=True)
        if self.use_cuda:
            interpolated = interpolated.cuda()

        # Calculate probability of interpolated examples
        prob_interpolated = self.D(interpolated)

        # Calculate gradients of probabilities with respect to examples
        gradients = torch_grad(outputs=prob_interpolated, inputs=interpolated,
                               grad_outputs=torch.ones(prob_interpolated.size()).cuda() if self.use_cuda else torch.ones(
                               prob_interpolated.size()),
                               create_graph=True, retain_graph=True)[0]

        # Gradients have shape (batch_size, num_channels, img_width, img_height),
        # so flatten to easily take norm per example in batch
        gradients = gradients.view(batch_size, -1)
        self.losses['gradient_norm'].append(gradients.norm(2, dim=1).mean().item())

        # Derivatives of the gradient close to 0 can cause problems because of
        # the square root, so manually calculate norm and add epsilon
        gradients_norm = torch.sqrt(torch.sum(gradients ** 2, dim=1) + 1e-12)

        # Return gradient penalty
        return self.gp_weight * ((gradients_norm - 1) ** 2).mean()

    def _train_epoch(self, data_loader):
        for i, data in enumerate(data_loader):
            self.num_steps += 1
            self._critic_train_iteration(data[0])
            # Only update generator every |critic_iterations| iterations
            if self.num_steps % self.critic_iterations == 0:
                    self._generator_train_iteration(data[0], self.gamma)

            if i % self.print_every == 0:
                
                print("Iteration {}".format(i + 1))
                print("D: {}".format(self.losses['D'][-1]))
                print("GP: {}".format(self.losses['GP'][-1]))
                print("Gradient norm: {}".format(self.losses['gradient_norm'][-1]))
                if self.num_steps > self.critic_iterations:
                    print("G: {}".format(self.losses['G'][-1]))
                    for i in range(self.nb_g):
                        print("G_{}: {}".format((i+1), self.losses['G_{}'.format(i+1)][-1]))

    def train(self, data_loader, epochs, save_training_gif=False):

        if save_training_gif:
            # Fix latents to see how image generation improves during training
            fixed_latents = Variable(self.G[0].sample_latent(64))
            if self.use_cuda:
                fixed_latents = fixed_latents.cuda()
            gen_training_progress_images = {i : [] for i in range(self.nb_g)}




        for epoch in range(epochs):

            
            start_time = time.time()
            print("\nEpoch {}".format(epoch + 1))
            self._train_epoch(data_loader)
            end_time = time.time()

            print("-------------------------------")
            print(f"Duration : {round((end_time - start_time),3)}s")
            print("-------------------------------")

            if save_training_gif:
                # Generate batch of images and convert to grid
                for index_g in range(self.nb_g) :
                    img_grid = make_grid(self.G[index_g](fixed_latents).cpu().data)
                    # Convert to numpy and transpose axes to fit imageio convention
                    # i.e. (width, height, channels)
                    img_grid = np.transpose(img_grid.numpy(), (1, 2, 0))
                    # Add image grid to training progress
                    gen_training_progress_images[index_g].append(img_grid)
                    
            if save_training_gif and epoch % 1 == 0:
                for index_g in range(self.nb_g) :
                    print("Img saving")
                    imageio.mimsave(f'./imgs_generated/training_{epoch}_epoch_generator_{index_g}.gif',
                                    gen_training_progress_images[index_g])

                            

    def sample_generator(self, num_samples, generator):

        latent_samples = Variable(generator.sample_latent(num_samples))
        if self.use_cuda:
            latent_samples = latent_samples.cuda()
        generated_data = generator(latent_samples)
        return generated_data

    def sample(self, num_samples):
        generated_data = self.sample_generator(num_samples)
        # Remove color channel
        return generated_data.data.cpu().numpy()[:, 0, :, :]