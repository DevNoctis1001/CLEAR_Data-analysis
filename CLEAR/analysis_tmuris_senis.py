import argparse
import builtins
import math
import os
import random
import shutil
import time
from typing import no_type_check
import warnings
from scanpy import preprocessing
from torch.autograd.grad_mode import F
from tqdm import tqdm
import numpy as np
import faiss

import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.optim
import torch.multiprocessing as mp
import torch.utils.data
import torch.utils.data.distributed
import torchvision.transforms as transforms
import torchvision.datasets as datasets
import torchvision.models as models

import pcl.loader
import pcl.builder

from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, silhouette_score
from sklearn.cluster import KMeans

from anndata import AnnData
import scanpy as sc
import pandas as pd

model_names = sorted(name for name in models.__dict__
    if name.islower() and not name.startswith("__")
    and callable(models.__dict__[name]))


parser = argparse.ArgumentParser(description='PyTorch scRNA-seq CLR Training')
parser.add_argument('--h5ad_data', type=str, default= "",
                    help='path to h5ad data')

parser.add_argument('-j', '--workers', default=32, type=int, metavar='N',
                    help='number of data loading workers (default: 32)')

parser.add_argument('--epochs', default=100, type=int, metavar='N',
                    help='number of total epochs to run')

parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')

parser.add_argument('-b', '--batch-size', default=512, type=int,
                    metavar='N',
                    help='mini-batch size (default: 256), this is the total '
                         'batch size of all GPUs on the current node when '
                         'using Data Parallel or Distributed Data Parallel')

parser.add_argument('--lr', '--learning-rate', default=5e-3, type=float,
                    metavar='LR', help='initial learning rate', dest='lr')


parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum of SGD solver')

parser.add_argument('--wd', '--weight-decay', default=1e-6, type=float,
                    metavar='W', help='weight decay (default: 1e-4)',
                    dest='weight_decay')

parser.add_argument('--s', '--save-freq', default=10, type=int,
                    metavar='N', help='Save frequency (default: 10)',
                    dest='save_freq')

parser.add_argument('-p', '--print-freq', default=100, type=int,
                    metavar='N', help='print frequency (default: 10)')

parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')


parser.add_argument('--seed', default=None, type=int,
                    help='seed for initializing training. ')
parser.add_argument('--gpu', default=None, type=int,
                    help='GPU id to use.')

parser.add_argument('--schedule', default=[100, 120], nargs='*', type=int,
                    help='learning rate schedule (when to drop lr by 10x), if use cos, then it will not be activated')

parser.add_argument('--low-dim', default=128, type=int,
                    help='feature dimension (default: 128)')
parser.add_argument('--pcl-r', default=1024, type=int,
                    help='queue size; number of negative pairs; needs to be smaller than num_cluster (default: 16384)')
parser.add_argument('--moco-m', default=0.999, type=float,
                    help='moco momentum of updating key encoder (default: 0.999)')

parser.add_argument('--temperature', default=0.2, type=float,
                    help='softmax temperature')


parser.add_argument('--cos', action='store_true',
                    help='use cosine lr schedule')

parser.add_argument('--num-cluster', default='7', type=str, 
                    help='number of clusters', dest="num_cluster")

parser.add_argument('--warmup-epoch', default=5, type=int,
                    help='number of warm-up epochs to only train with InfoNCE loss')

parser.add_argument('--exp-dir', default='experiment_pcl', type=str,
                    help='experiment directory')

parser.add_argument('--CPM', action="store_true",
                    help='do count per million operation for raw counts')
parser.add_argument("--log", action="store_true",
                    help='Whether do log operation before preprocessing')
parser.add_argument("--highlyGene", action="store_true",
                    help="Whether select highly variable gene")
                    
parser.add_argument("--tissue", type=str,
                    help="Run experiments on the tissue")

tmuris_senis_tissue = ['Aorta',
 'BAT',
 'Bladder',
 'Brain_Myeloid',
 'Brain_Non-Myeloid',
 'Diaphragm',
 'GAT',
 'Heart',
 'Kidney',
 'Large_Intestine',
 'Limb_Muscle',
 'Liver',
 'Lung',
 'MAT',
 'Mammary_Gland',
 'Marrow',
 'Pancreas',
 'SCAT',
 'Skin',
 'Spleen',
 'Thymus',
 'Tongue',
 'Trachea']





def main():
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which can slow down your training considerably! '
                      'You may see unexpected behavior when restarting '
                      'from checkpoints.')

    if args.gpu is not None:
        warnings.warn('You have chosen a specific GPU. This will completely '
                      'disable data parallelism.')

    
    args.num_cluster = args.num_cluster.split(',')
    

    if not os.path.exists(args.exp_dir):
        os.mkdir(args.exp_dir)
    


    main_worker(args.gpu, args)


def main_worker(gpu, args):
    args.gpu = gpu
    
    if args.gpu is not None:
        print("Use GPU: {} for training".format(args.gpu))


    processed_adata = preprocess_dataset(args.h5ad_data, select_tissue="BAT", select_highly_variable_gene=args.highlyGene, do_CPM=args.CPM, do_log=args.log)
    
    # debug, use the preprocessed mouse data
    #processed_adata = sc.read("/home/hanw/projects/scRNA-seq/data/Baron_data_2016/mouse_normalized.h5ad")
    #proc.14915583.erressed_adata.obs['x'] = processed_adata.obs['assigned_cluster']

    args_transformation = {
        # crop
        # without resize, it's better to remove crop
        
        # mask
        'mask_percentage': 0.2,
        'apply_mask_prob': 0.5,
        
        # (Add) gaussian noise
        'noise_percentage': 0.8,
        'sigma': 0.2,
        'apply_noise_prob': 0.5,

        # inner swap
        'swap_percentage': 0.1,
        'apply_swap_prob': 0.5,
        
        # cross over with 1
        'cross_percentage': 0.25,
        'apply_cross_prob': 0.5,
        
        # cross over with many
        'change_percentage': 0.25,
        'apply_mutation_prob': 0.5
    }


    train_dataset = pcl.loader.scRNAMatrixInstance(
        adata=processed_adata,
        transform=True,
        args_transformation=args_transformation
        )
    eval_dataset = pcl.loader.scRNAMatrixInstance(
        adata=processed_adata,
        transform=False
        )

    if train_dataset.num_cells < 512:
        args.batch_size = train_dataset.num_cells
        args.pcl_r = train_dataset.num_cells
    
    args.num_cluster = len(train_dataset.unique_label) 

    # create model
    print("=> creating model 'MLP'")
    model = pcl.builder.MoCo(
        pcl.builder.MLPEncoder,
        int(train_dataset.num_genes),
        args.low_dim, args.pcl_r, args.moco_m, args.temperature)
    print(model)

    
    
    torch.cuda.set_device(args.gpu)
    model = model.cuda(args.gpu)
       
    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().cuda(args.gpu)

    optimizer = torch.optim.SGD(model.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay)

    # optionally resume from a checkpoint
    if args.resume:
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '{}'".format(args.resume))
            if args.gpu is None:
                checkpoint = torch.load(args.resume)
            else:
                # Map model to be loaded to specified single gpu.
                loc = 'cuda:{}'.format(args.gpu)
                checkpoint = torch.load(args.resume, map_location=loc)
            args.start_epoch = checkpoint['epoch']
            model.load_state_dict(checkpoint['state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            print("=> loaded checkpoint '{}' (epoch {})"
                  .format(args.resume, checkpoint['epoch']))
        else:
            print("=> no checkpoint found at '{}'".format(args.resume))

    cudnn.benchmark = True

    train_sampler = None
    eval_sampler = None
    


    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=(train_sampler is None),
        num_workers=args.workers, pin_memory=True, sampler=train_sampler, drop_last=True)
    
    # dataloader for center-cropped images, use larger batch size to increase speed
    eval_loader = torch.utils.data.DataLoader(
        eval_dataset, batch_size=args.batch_size*5, shuffle=False,
        sampler=eval_sampler, num_workers=args.workers, pin_memory=True)
    

    # train the model
    for epoch in range(args.start_epoch, args.epochs):
        
        cluster_result = None
        if epoch>=args.warmup_epoch and epoch%args.save_freq==0:
            # compute momentum features for center-cropped images
            features, labels = compute_features(eval_loader, model, args)         

            if epoch%args.save_freq==0 and epoch<300:

                features[np.linalg.norm(features,axis=1)>1.5] /= 2 #account for the few samples that are computed twice  
                
                np.save(os.path.join(args.exp_dir, f'features_{epoch}.npy'), features)

                #cluster_result = run_kmeans(features,args)  #run kmeans clustering on master node
                # save the clustering result
                #torch.save(cluster_result,os.path.join(args.exp_dir, f'clusters_{epoch}_{args.num_cluster}'))  
                #ari = run_ARI(labels, cluster_result)
                sklearn_kmeans_metrics = run_sklearn_kmeans(features, labels, int(args.num_cluster))
            
                # use the leiden algorithm for clustering
                #leiden_ari = run_scanpy_leiden(features, labels)

                with open(os.path.join(args.exp_dir, f'result.txt'), "a") as f:
                    f.writelines(f"{epoch}\t" + '\t'.join((str(elem) for elem in sklearn_kmeans_metrics)) +"\t" + str(acc) + "\n")


        adjust_learning_rate(optimizer, epoch, args)

        # train for one epoch
        acc = train(train_loader, model, criterion, optimizer, epoch, args, None)

        # if (epoch+1)%5==0 and (not args.multiprocessing_distributed or (args.multiprocessing_distributed
        #         and args.rank % ngpus_per_node == 0)):
        #     save_checkpoint({
        #         'epoch': epoch + 1,
        #         'arch': args.arch,
        #         'state_dict': model.state_dict(),
        #         'optimizer' : optimizer.state_dict(),
        #     }, is_best=False, filename='{}/checkpoint_{:04d}.pth.tar'.format(args.exp_dir,epoch))


def train(train_loader, model, criterion, optimizer, epoch, args, cluster_result=None):
    batch_time = AverageMeter('Time', ':6.3f')
    data_time = AverageMeter('Data', ':6.3f')
    losses = AverageMeter('Loss', ':.4e')
    acc_inst = AverageMeter('Acc@Inst', ':6.2f')   
    
    progress = ProgressMeter(
        len(train_loader),
        [batch_time, data_time, losses, acc_inst],
        prefix="Epoch: [{}]".format(epoch))

    # switch to train mode
    model.train()

    end = time.time()
    for i, (images, index, label) in enumerate(train_loader):
        # measure data loading time
        data_time.update(time.time() - end)

        #import pdb; pdb.set_trace()

        if args.gpu is not None:
            images[0] = images[0].cuda(args.gpu, non_blocking=True)
            images[1] = images[1].cuda(args.gpu, non_blocking=True)
                
        # compute output
        output, target, output_proto, target_proto = model(im_q=images[0], im_k=images[1], cluster_result=cluster_result, index=index)
        
        # InfoNCE loss
        loss = criterion(output, target)  

        losses.update(loss.item(), images[0].size(0))
        acc = accuracy(output, target)[0] 
        acc_inst.update(acc[0], images[0].size(0))

        # compute gradient and do SGD step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # measure elapsed time 
        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.print_freq == 0:
            progress.display(i)

    return acc_inst.avg
            
def compute_features(eval_loader, model, args):
    print('Computing features...')
    model.eval()
    features = []
    labels = []

    for i, (images, index, label) in enumerate(eval_loader):
        images = images.cuda()
        with torch.no_grad():
            feat = model(images, is_eval=True) 
        feat_pred = feat.data.cpu().numpy()
        label_true = label
        features.append(feat_pred)
        labels.append(label_true)
    
    features = np.concatenate(features, axis=0)
    labels = np.concatenate(labels, axis=0)

    return features, labels

def run_ARI(labels, cluster_result):
    pred = cluster_result['im2cluster'][0].cpu().numpy()
    print(pred.shape)
    print(labels.shape)
    ari = adjusted_rand_score(labels_true=labels, labels_pred=pred)
    print(f"The ARI is {ari}\n")
    return ari
    
def run_sklearn_kmeans(features, labels, num_cluster):
    best_ari = 0
    best_nmi = 0
    for random_seed in range(5):
        kmeans = KMeans(n_clusters=num_cluster, random_state=random_seed).fit(features)
        ari = adjusted_rand_score(labels_true=labels, labels_pred=kmeans.labels_)
        nmi = normalized_mutual_info_score(labels_true=labels, labels_pred=kmeans.labels_)
        if ari > best_ari:
            best_ari = ari
            best_seed = random_seed
        if nmi > best_nmi:
            best_nmi = nmi

    silhou = silhouette_score(features, labels=labels)

    print(f"The sklearn ARI is {best_ari} at sklearn seed {best_seed}\n")
    print(f"The sklearn NMI is {best_nmi} \n")
    print(f"The silhouette coefficient is {silhou}\n")
    
    
    return [best_ari, best_nmi, silhou]



def run_scanpy_leiden(features, labels):
    adata = AnnData(X=None)
    for i,z in enumerate(features.T):
        adata.obs[f'Z_{i}'] = z
    adata.obsm["X_scVI"] = features
    sc.pp.neighbors(adata, use_rep="X_scVI", n_neighbors=20)
    #sc.tl.umap(adata, min_dist=0.3)
    sc.tl.leiden(adata, key_added="leiden_scVI", resolution=0.8)


def run_kmeans(x, args):
    """
    Args:
        x: data to be clustered
    """
    
    print('performing kmeans clustering')
    results = {'im2cluster':[],'centroids':[],'density':[]}
    
    for seed, num_cluster in enumerate(args.num_cluster):
        # intialize faiss clustering parameters
        d = x.shape[1]
        k = int(num_cluster)
        clus = faiss.Clustering(d, k)
        clus.verbose = True
        clus.niter = 20
        clus.nredo = 5
        clus.seed = seed
        clus.max_points_per_centroid = 1000
        clus.min_points_per_centroid = 10

        res = faiss.StandardGpuResources()
        cfg = faiss.GpuIndexFlatConfig()
        cfg.useFloat16 = False
        cfg.device = args.gpu    
        index = faiss.GpuIndexFlatL2(res, d, cfg)  

        clus.train(x, index)   

        D, I = index.search(x, 1) # for each sample, find cluster distance and assignments
        im2cluster = [int(n[0]) for n in I]
        
        # get cluster centroids
        centroids = faiss.vector_to_array(clus.centroids).reshape(k,d)
        
        # sample-to-centroid distances for each cluster 
        Dcluster = [[] for c in range(k)]          
        for im,i in enumerate(im2cluster):
            Dcluster[i].append(D[im][0])
        
        # concentration estimation (phi)        
        density = np.zeros(k)
        for i,dist in enumerate(Dcluster):
            if len(dist)>1:
                d = (np.asarray(dist)**0.5).mean()/np.log(len(dist)+10)            
                density[i] = d     
                
        #if cluster only has one point, use the max to estimate its concentration        
        dmax = density.max()
        for i,dist in enumerate(Dcluster):
            if len(dist)<=1:
                density[i] = dmax 

        density = density.clip(np.percentile(density,10),np.percentile(density,90)) #clamp extreme values for stability
        density = args.temperature*density/density.mean()  #scale the mean to temperature 
        
        # convert to cuda Tensors for broadcast
        centroids = torch.Tensor(centroids).cuda()
        centroids = nn.functional.normalize(centroids, p=2, dim=1)    

        im2cluster = torch.LongTensor(im2cluster).cuda()               
        density = torch.Tensor(density).cuda()
        
        results['centroids'].append(centroids)
        results['density'].append(density)
        results['im2cluster'].append(im2cluster)    
        
    return results

    
def save_checkpoint(state, is_best, filename='checkpoint.pth.tar'):
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, 'model_best.pth.tar')


class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self, name, fmt=':f'):
        self.name = name
        self.fmt = fmt
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

    def __str__(self):
        fmtstr = '{name} {val' + self.fmt + '} ({avg' + self.fmt + '})'
        return fmtstr.format(**self.__dict__)


class ProgressMeter(object):
    def __init__(self, num_batches, meters, prefix=""):
        self.batch_fmtstr = self._get_batch_fmtstr(num_batches)
        self.meters = meters
        self.prefix = prefix

    def display(self, batch):
        entries = [self.prefix + self.batch_fmtstr.format(batch)]
        entries += [str(meter) for meter in self.meters]
        print('\t'.join(entries))

    def _get_batch_fmtstr(self, num_batches):
        num_digits = len(str(num_batches // 1))
        fmt = '{:' + str(num_digits) + 'd}'
        return '[' + fmt + '/' + fmt.format(num_batches) + ']'


def adjust_learning_rate(optimizer, epoch, args):
    """Decay the learning rate based on schedule"""
    lr = args.lr
    if args.cos:  # cosine lr schedule
        lr *= 0.5 * (1. + math.cos(math.pi * epoch / args.epochs))
    else:
        # stepwise lr schedule
        for milestone in args.schedule:
            lr *= 0.1 if epoch >= milestone else 1.
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def accuracy(output, target, topk=(1,)):
    """Computes the accuracy over the k top predictions for the specified values of k"""
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


def preprocess_dataset(adata_dir: str="", 
                       select_tissue: str="all",
                       select_highly_variable_gene: bool=False,
                       do_CPM: bool=True,
                       do_log: bool=True):

    # read the count matrix from the path
    adata = sc.read(adata_dir)
    if select_tissue == "all":
        pass
    else:
        assert select_tissue in tmuris_senis_tissue
        adata = adata[adata.obs['tissue'] == select_tissue]
    
    adata.obs['x'] = adata.obs['cell_ontology_class']

    # do preprocessing on the adata file
    # basic filtering, filter the genes and cells
    #sc.pp.filter_cells(adata, min_counts=5000)
    sc.pp.filter_cells(adata, min_genes=200)

    sc.pp.filter_genes(adata, min_cells=3)

    # calculate the qc metrics, then do the normalization

    # filter the mitochondrial genes
    adata.var['mt'] = adata.var_names.str.startswith('MT-')  # annotate the group of mitochondrial genes as 'mt'
    sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
    low_MT_MASK = (adata.obs.pct_counts_mt < 5)
    adata = adata[low_MT_MASK]

    # filter the ERCC spike-in RNAs
    adata.var['ERCC'] = adata.var_names.str.startswith('ERCC-')  # annotate the group of ERCC spike-in as 'ERCC'
    sc.pp.calculate_qc_metrics(adata, qc_vars=['ERCC'], percent_top=None, log1p=False, inplace=True)
    low_ERCC_mask = (adata.obs.pct_counts_ERCC < 10)
    adata = adata[low_ERCC_mask]
  
    
    import pdb; pdb.set_trace()
    random_dropout(adata)

    if select_highly_variable_gene and not do_log:
        sc.pp.highly_variable_genes(adata, flavor="seurat_v3", n_top_genes=5000)
        adata = adata[:, adata.var.highly_variable]

    sc.pp.normalize_total(adata, target_sum=1e4, exclude_highly_expressed=True)
    adata.raw = adata 
    
    if np.max(adata.X>100) and do_log: 
        sc.pp.log1p(adata)

    # before normalization, we can select the most variant genes
    if select_highly_variable_gene and do_log:
        sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
        adata = adata[:, adata.var.highly_variable]

    # after that, we do the linear scaling
    
    #sc.pp.normalize_total(adata, target_sum=1e4, exclude_highly_expressed=True)
    

    # log operations and scale operations will hurt the 
    # contrastive between the data
    
    sc.pp.scale(adata,max_value=10,zero_center=True)
    #adata[np.isnan(adata.X)] = 0 
    # adata_max = np.max(adata.X)
    # adata_min = np.min(adata.X)
    # adata.X = (adata.X - adata_min)/(adata_max - adata_min)


    

    return adata
    






if __name__ == '__main__':
    main()
