# CLEAR

## Introduction

CLEAR (self-supervised **C**ontrastive **LEA**rning framework for sc**R**NA-seq) software provides a self-supervised learning based integrative single-cell RNA-seq data analysis tool. It overcomes the heterogeneity of the experimental data with a specifically designed representation learning task and thus can handle batch effects remove, dropout events, and time-trajectory inference better.

## Installation

The package can be installed based by `git `. Test environment is CentOS 7 operation system, Nvidia TITAN X GPU.

### 1. Git clone from github

```
git clone https://github.com/ml4bio/CLEAR.git
cd ~/CLEAR/
```

### 2. Use virtual environment with Anaconda
The main environment for CLEAR can be installed with this command:
```
conda env create -f environment.yml
```

## Quick Running

### 1. Prepare Dataset

First, original input datasets should be preprocessed  before feeding into CLEAR. 
The datasets used in our paper include two file formats: h5ad and rds.
In the example, we will concentrate on h5ad files and take "tabula-muris-senis-facs-processed-official-annotations-Bladder" for instance.
There are two kinds of input data format: rds and h5ad. The preprocessing step will , so we should transform them into csv files respectively. 
In the following examples, I will use baron-mouse.rds and abula-muris-senis-facs-processed-official-annotations-Diaphragm.h5ad as references. 

(1) download dataset.
You can either download all of them with the script "download-data.sh" in the "data" folder or use the command in it to download specific dataset.
Here, we take "deng.rds" dataset for example.
```
wget https://ndownloader.figshare.com/files/23872610 -O data/original/h5ad/tmsfpoa-Bladder.h5ad
```

(2) generate preprocessed h5ad file.
```
python preprocess/generate_preprocessed_h5ad.py --input_h5ad_path="./data/original/h5ad/tmsfpoa-Bladder.h5ad" --save_h5ad_dir="./data/preprocessed/h5ad/" --log --drop_prob=0
```
As for rds files, you can transform them into csv files by the script "rds_to_csv.py" in the "preprocess" folder and then preprocessed them with the same above script.
The required R environment of this R script need to be set up by yourselves, of which the core package is "SingleCellExperiment"

### 2. Apply CLEAR

we can apply CLEAR with the following command:
```
python CLEAR.py --input_h5ad_path="./data/preprocessed/h5ad/tmsfpoa-Bladder_preprocessed.h5ad" --obs_label_colname="cell_ontology_class" --epochs 100 --lr 0.01 --batch_size 512 --pcl_r 1024 --cos --gpu 0
```
Note: output files are saved in ./result/CLEAR, including embeddings (feature.csv), ground truth labels (gt_label.csv if applicable), cluster results (pd_label.csv if applicable) and some log files (log)


## Citation

Han, W., Cheng, Y., Chen, J., Zhong, H., Hu, Z., Chen, S., . . . Li, Y. (2021). Self-supervised contrastive learning for integrative single cell RNA-seq data analysis. bioRxiv, 2021.2007.2026.453730. doi:10.1101/2021.07.26.453730

