#!/usr/bin/env python3.6

import torch

import sys
import os
import numpy as np
from tqdm import tqdm

from easypbr  import *
from dataloaders import *
from latticenet  import TrainParams
from latticenet  import ModelParams
from latticenet  import EvalParams
from lattice.lattice_py import LatticePy
from lattice.models import *

from callbacks.callback import *
from callbacks.viewer_callback import *
from callbacks.visdom_callback import *
from callbacks.state_callback import *
from callbacks.phase import *

#semantickitt on infcuda2


#config_file="lnn_eval_semantic_kitti.cfg"
config_file="lnn_eval_scannet.cfg"

torch.manual_seed(0)

# #initialize the parameters used for training
eval_params=EvalParams.create(config_file) 
model_params=ModelParams.create(config_file)    

def write_prediction(pred_softmax, cloud, pred_path):
    mesh_pred=cloud.clone()
    l_pred=pred_softmax.detach().argmax(axis=1).cpu().numpy()
    #l_pred=l_pred.unsqueeze(1)
    l_pred = np.expand_dims(l_pred, axis=1)
    #print("l_pred has shape ", l_pred.shape)
    mesh_pred.color_from_label_indices(l_pred)
    mesh_pred.L_pred=l_pred
    mesh_pred.save_to_file(pred_path)
    
def write_gt(cloud, gt_path):
    mesh_gt=cloud.clone()
    mesh_gt.color_from_label_indices(cloud.L_gt)
    mesh_gt.save_to_file(gt_path)


def create_loader(dataset_name, config_file):
    if(dataset_name=="semantickitti"):
        loader=DataLoaderSemanticKitti(config_file)
    elif dataset_name=="shapenet":
        loader=DataLoaderShapeNetPartSeg(config_file)
    elif dataset_name=="scannet":
        loader=DataLoaderScanNet(config_file)
    else:
        err="Datset name not recognized. It is " + dataset_name
        sys.exit(err)

    return loader


def run():
    config_path=os.path.join( os.path.dirname( os.path.realpath(__file__) ) , '../config', config_file)
    if eval_params.with_viewer():
        view=Viewer.create(config_path)

    first_time=True

    #torch stuff 
    lattice=LatticePy()
    lattice.create(config_path, "splated_lattice")

    cb = CallbacksGroup([
        # LatticeSigmaCallback() #TODO
        # ViewerCallback(),
        # VisdomCallback(),
        StateCallback() #changes the iter nr epoch nr,
    ])
    #create loaders
    loader_test=create_loader(eval_params.dataset_name(), config_path)
    loader_test.set_mode_test()
    if isinstance(loader_test, DataLoaderSemanticKitti):
        loader_test.set_sequence("all") #for smenantic kitti in case the train one only trains on only one sequence we still want to test on all
    # if isinstance(loader_test, DataLoaderScanNet):
        # loader_test.set_mode_validation() #scannet doesnt have a ground truth for the test set so we use the validation set
    loader_test.start()
    #create phases
    phases= [
        Phase('test', loader_test, grad=False)
    ]
    #model 
    model=LNN(loader_test.label_mngr().nr_classes(), model_params, False, False).to("cuda")

    predictions_list=[]
    scores_list=[]

    while True:

        for phase in phases:
            cb.epoch_started(phase=phase)
            cb.phase_started(phase=phase)
            model.train(phase.grad)

            pbar = tqdm(total=phase.loader.nr_samples())
            while ( phase.samples_processed_this_epoch < phase.loader.nr_samples()):
                
                if(phase.loader.has_data()): 
                    cloud=phase.loader.get_cloud()

                    is_training = phase.grad

                    #forward
                    with torch.set_grad_enabled(is_training):
                        cb.before_forward_pass(lattice=lattice) #sets the appropriate sigma for the lattice
                        positions, values, target = prepare_cloud(cloud, model_params) #prepares the cloud for pytorch, returning tensors alredy in cuda
                        pred_logsoftmax, pred_raw =model(lattice, positions, values)


                        #debug 
                        #l_pred=pred_logsoftmax.detach().argmax(axis=1).cpu().numpy()
                        #nr_zeros= l_pred ==0
                        #nr_zeros=nr_zeros.sum()
                        #print("nr_zeros is ", nr_zeros)
                      

                        #if its the first time we do a forward on the model we need to load here the checkpoint
                        if first_time:
                            first_time=False
                            #TODO load checkpoint
                            # now that all the parameters are created we can fill them with a model from a file
                            model.load_state_dict(torch.load(eval_params.checkpoint_path()))
                            #need to rerun forward with the new parameters to get an accurate prediction
                            pred_logsoftmax, pred_raw =model(lattice, positions, values)

                        cb.after_forward_pass(pred_softmax=pred_logsoftmax, target=target, cloud=cloud, loss=0, loss_dice=0, phase=phase, lr=0) #visualizes the prediction 
                        pbar.update(1)

                        if eval_params.do_write_predictions():
                            if isinstance(loader_test, DataLoaderScanNet):
                                to_save_path=os.path.join(eval_params.output_predictions_path(), cloud.name )
                                print("saving in ", to_save_path)
                                pred_path=to_save_path+"_pred.ply"
                                gt_path=to_save_path+"_gt.ply"
                                #write_prediction(pred_logsoftmax, cloud, pred_path)
                                #write_gt(cloud, gt_path)


                                print("got cloud with name ", cloud.name)
                                l_pred=pred_logsoftmax.detach().argmax(axis=1).cpu().numpy()
                                
                                #debug 
                                nr_zeros= l_pred ==0
                                nr_zeros=nr_zeros.sum()
                                print("nr_zeros is ", nr_zeros)
                                print("pred_logsoftmax has shape ", pred_logsoftmax.shape)

                                cloud.L_pred=l_pred
                                path_for_eval=os.path.join(eval_params.output_predictions_path(), "for_evaluation")
                                os.makedirs(path_for_eval, exist_ok=True)
                                loader_test.write_for_evaluating_on_scannet_server(cloud, path_for_eval)

                                

                            if isinstance(loader_test, DataLoaderSemanticKitti):
                                # full path in which we save the cloud depends on the data loader. If it's semantic kitti we save also with the sequence, if it's scannet
                                cloud_path_full=cloud.m_disk_path
                                # cloud_path=os.path.join(os.path.dirname(cloud_path), "../../")
                                basename=os.path.splitext(os.path.basename(cloud_path_full))[0]
                                cloud_path_base=os.path.abspath(os.path.join(os.path.dirname(cloud_path_full), "../../"))
                                cloud_path_head=os.path.relpath( cloud_path_full, cloud_path_base  )
                                # print("cloud_path_head is ", cloud_path_head)
                                # print("basename is ", basename)
                                path_before_file=os.path.join(eval_params.output_predictions_path(), os.path.dirname(cloud_path_head))
                                os.makedirs(path_before_file, exist_ok=True)
                                to_save_path=os.path.join(path_before_file, basename )
                                print("saving in ", to_save_path)
                                # pred_path=to_save_path+"_pred.ply"
                                # gt_path=to_save_path+"_gt.ply"
                                # print("writing prediction to ", pred_path)
                                # write_prediction(pred_softmax, cloud, pred_path)
                                # write_gt(cloud, gt_path)


                                #write labels file (just a file containing for each point the predicted label)
                                l_pred=pred_logsoftmax.detach().argmax(axis=1).cpu().numpy()
                                l_pred = l_pred.reshape((-1))
                                l_pred = l_pred.astype(np.uint32)
                                labels_file= os.path.join(path_before_file, (basename+".label") )                
                                l_pred.tofile(labels_file)
                                # with open(labels_file, 'w') as f:
                                    # for i in range(l_pred.shape[0]):
                                        # line= str(l_pred[i]) + "\n"
                                        # f.write(line)

                                
                                # #write GT labels file (just a file containing for each point the predicted label)
                                # gt = np.squeeze(cloud.L_gt)
                                # labels_file= os.path.join(path_before_file, (basename+".gt") )                
                                # with open(labels_file, 'w') as f:
                                #     for i in range(gt.shape[0]):
                                #         line= str(gt[i]) + "\n"
                                #         f.write(line)

                                # #check the predictions from tangentconv and get how much different we are from it. We want to show an image of the biggest change in accuracy
                                # #we want the difference to gt to be small and the difference to tangent conv to be big
                                # tangentconv_path="/home/user/rosu/data/semantic_kitti/predictions_from_related_work/tangent_conv_semantic_kitti_single_frame_final_predictions_11_21"
                                # cloud_path_without_seq=os.path.abspath(os.path.join(os.path.dirname(cloud_path_full), "../"))
                                # cloud_path_with_seq=os.path.relpath( cloud_path_full, cloud_path_without_seq  )
                                # seq=os.path.dirname(cloud_path_with_seq)
                                # path_to_tangentconv_pred=os.path.join(tangentconv_path, seq,  (basename + ".label")  )
                                # print("path_to_tangentconv_pred", path_to_tangentconv_pred)

                                # f = open(path_to_tangentconv_pred, "r")
                                # tangentconv_labels = np.fromfile(f, dtype=np.uint32)

                                # #compute score 
                                # l_pred=pred_softmax.detach().argmax(axis=1).cpu().numpy()
                                # gt = np.squeeze(cloud.L_gt)
                                # # print("gt shape", gt.shape)
                                # # print("l_pref shape", l_pred.shape)
                                # # print("tangentconv_labels shape", tangentconv_labels.shape)
                                # point_is_valid = gt!=0
                                # nr_valid_points=point_is_valid.sum()
                                # point_is_different_than_gt = gt != l_pred
                                # diff_to_gt = (np.logical_and(point_is_different_than_gt, point_is_valid)).sum()
                                # point_is_different_than_tangentconv = tangentconv_labels != l_pred
                                # diff_to_tangentconv = (np.logical_and(point_is_different_than_tangentconv, point_is_valid)).sum()
                                # # print("diff to gt  is ", diff_to_gt)
                                # # print("diff to tangentconv  is ", diff_to_tangentconv)
                                # score=diff_to_tangentconv-diff_to_gt ##we try to maximize this score
                                # score /=nr_valid_points #normalize by the number of points becuase otherwise the score will be squeed towards grabbing point clouds that are just gigantic because they have more points
                                # print("score is ", score)

                                # #store the score and the path in a list
                                # predictions_list.append(cloud_path_head)
                                # scores_list.append(score)
                                # # print("predictions_list",predictions_list)
                                # # print("score_lists",scores_list)

                                # #sort based on score https://stackoverflow.com/a/6618543
                                # predictions_sorted=[predictions_list for _,predictions_list in sorted(zip(scores_list,predictions_list))]
                                # scores_sorted=np.sort(scores_list)
                                # # print("predictions_sorted",predictions_sorted)
                                # # print("scores_sorted",scores_sorted)
                                # # print("predictions_list",predictions_list)
                                # # print("score_lists",scores_list)

                                # #write the sorted predictions to file 
                                # best_predictions_file=os.path.join(eval_params.output_predictions_path(), "best_preds.txt")
                                # with open(best_predictions_file, 'w') as f:
                                #     for i in range(len(predictions_sorted)):
                                #         line= predictions_sorted[i] +  "    score: " +  str(scores_sorted[i]) + "\n"
                                #         f.write(line)


                if phase.loader.is_finished():
                    pbar.close()
                    cb.epoch_ended(phase=phase, model=model, save_checkpoint=False, checkpoint_path="" ) 
                    cb.phase_ended(phase=phase) 
                    return


                if eval_params.with_viewer():
                    view.update()


def main():
    run()



if __name__ == "__main__":
     main()  # This is what you would have, but the following is useful:

    # # These are temporary, for debugging, so meh for programming style.
    # import sys, trace

    # # If there are segfaults, it's a good idea to always use stderr as it
    # # always prints to the screen, so you should get as much output as
    # # possible.
    # sys.stdout = sys.stderr

    # # Now trace execution:
    # tracer = trace.Trace(trace=1, count=0, ignoredirs=["/usr", sys.prefix])
    # tracer.run('main()'
