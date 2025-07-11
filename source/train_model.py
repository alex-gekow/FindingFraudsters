import pandas as pd
import os, gc, sys
import numpy as np
import matplotlib.pyplot as plt
import xgboost as xgb

from sklearn.metrics import roc_curve,auc, precision_recall_curve
from sklearn.model_selection import KFold
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay, precision_score

from source.data_loader import *
from source.model_selector import ModelSelector

def get_path(file_name):
    return_dir = os.path.dirname(os.path.abspath(__file__))
    return_dir = os.path.dirname(return_dir)
    return_dir = os.path.join(return_dir, file_name)
    return return_dir

def plot_roc(y_true, y_prob, name=""):
     
    # Compute ROC curve and AUC
    fpr, tpr, threshold = roc_curve(y_true.astype(int).values, y_prob)
    optimal_idx = np.argmax(tpr - fpr)
    optimal_threshold = threshold[optimal_idx]
    roc_auc = auc(fpr, tpr)
    print("roc auc", roc_auc, "at threshold",optimal_threshold)

    plt.figure()
    plt.plot(fpr, tpr, label=f'ROC curve (AUC = {roc_auc:.2f})')
    plt.plot([0, 1], [0, 1], 'k--')  # Diagonal line
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.title('Receiver Operating Characteristic')
    plt.legend(loc='lower right')
    plt.grid(True)
    output_path = get_path(f"outputs/roc_{name}.png")
    plt.savefig(output_path)
    return optimal_threshold

def plot_pr(y_true, y_prob, name=""):
    # Compute ROC curve and AUC
    precision, recall, threshold = precision_recall_curve(y_true.astype(int).values, y_prob)
    f1_scores = 2 * precision * recall / (precision + recall + 1e-8)

    best_index = np.argmax(f1_scores)
    optimal_threshold = threshold[best_index]

    roc_auc = auc(recall, precision)
    print(f"Best F1: {f1_scores[best_index]:.4f} at threshold: {optimal_threshold:.4f}")
    print("auprc", roc_auc)
    plt.figure()
    plt.plot(recall,precision, label=f'ROC curve (AUPRC = {roc_auc:.2f})')
    plt.plot([0, 1], [1, 0], 'k--')  # Diagonal line
    plt.xlabel('recall')
    plt.ylabel('precision')
    plt.title('Receiver Operating Characteristic')
    plt.legend(loc='lower right')
    plt.grid(True)

    output_path = get_path(f"outputs/precision_recall_{name}.png")
    plt.savefig(output_path)

    return optimal_threshold

def plot_confusion_matrix(y_true, y_pred, threshold, name=""):
    y_pred = y_pred > threshold
    cm = confusion_matrix(y_true, y_pred)
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=[0, 1])
    output_path = get_path(f"outputs/confusion_matrix_{name}.png")
    disp.plot(cmap='Blues').figure_.savefig(output_path)

def main():

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--transaction', type=str, default='train_transactions.csv', help="path to transaction csv")
    parser.add_argument('-i', '--id', type=str, default=None, help='path to identity csv. Defaults to None')
    parser.add_argument('-m', '--model', type=str, default='model.json', help="Name of input/output model including file extension. (i.e model.json)")
    parser.add_argument('--train_test_split', type=float, default=-1, help="Train test split. Default is to train on all data")
    parser.add_argument('--tr_cols', type=str, default=None, help="Text file containing a list of variables to keep in transaction dataframe")
    parser.add_argument('--id_cols', type=str, default=None, help="Text file containing a list of variables to keep in identity dataframe")
    parser.add_argument('--test', action='store_true', help="Test a model (instead of train) on the test dataset which does not include isFraud variable.")
    parser.add_argument('--model_type', type=str, choices=['xgboost', 'lightgbm', 'catboost'], default='xgboost', help='Model type to use')
    parser.add_argument('--gpu', action='store_true', help='Use GPU')

    
    args = parser.parse_args()
    modelSelector = ModelSelector(model_type=args.model_type, use_gpu=args.gpu, max_depth=12, n_estimators=500, eval_metric='aucpr', learning_rate=0.02,
                                  subsample=0.8,colsample_bytree=0.4,early_stopping_rounds=10,missing=-999)
    
    dataLoader = DataLoader()

    dataLoader.load_csv(args.transaction, args.id, args.tr_cols, args.id_cols, args.test)

    # add additional features
    dataLoader.add_transaction_features()
    dataLoader.add_uid()
    dataLoader.transaction_in_window()
    dataLoader.add_key_match()

    # aggregate and frequency encoding features
    columns_to_encode = []
    columns_to_encode.append("TransactionAmt")
    columns_to_encode.append("TransactionDT")

    d_columns = [d for d in dataLoader.df.columns if d.startswith("D") and len(d) < 4]

    columns_to_encode += d_columns
    dataLoader.encode_AG('uid', columns_to_encode)

    columns_to_encode = ["addr1", "card1", "card2", "card3", "P_emaildomain", "R_emaildomain"]
    dataLoader.encode_FE(columns_to_encode)

    df = dataLoader.df
    # Ensure any columns we've added are correctly cast
    for col in df.select_dtypes(include=['object','bool']).columns:
            df[col], _ = pd.factorize(df[col])    

    df = df.sort_values(by='TransactionDT', ascending=True)
    
    if args.test:
        TransactionID = df['TransactionID']
        if 'uid' in df.columns:
            df = df.drop(columns=['TransactionID', 'uid', "addr1", "D1", "D1n"])
        else:
            df = df.drop(columns=['TransactionID', "addr1", "D1", "D1n"])
        model_path = get_path(f"models/{args.model}")
        modelSelector.load_model(model_path)
        y_prob = modelSelector.predict(df)

        output_df = pd.DataFrame(data=zip(TransactionID,y_prob), columns=["TransactionID", "isFraud"])
        output_path = get_path(f"outputs/predictions_{args.model}.csv")
        output_df.to_csv(output_path, index=False)

        return


    # Prepare inputs and targets for training
    y_train = df["isFraud"]
    if 'uid' in set(df.columns):
        x_train = df.drop(columns=["isFraud", "uid", "TransactionID", "addr1", "D1", "D1n"])
    else:
        x_train = df.drop(columns=["isFraud", "TransactionID", "addr1", "D1", "D1n"])

    if args.train_test_split > 0:
        from sklearn.model_selection import train_test_split
        
        x_train, x_test, y_train, y_test = train_test_split(x_train, y_train, test_size=args.train_test_split, stratify=y_train)
        # x_train, x_test, y_train, y_test = train_test_split(x_train, y_train, test_size=args.train_test_split, stratify=None, shuffle=False) # train and test using temporally separated data
    else:
        x_test, y_test = None, None

    modelSelector.fit(x_train, y_train, x_test, y_test)

    models_dir = get_path(f"models/{args.model}")
    print("Saving model to ",models_dir)

    modelSelector.save_model(models_dir)

    if args.train_test_split > 0:
        y_prob = modelSelector.predict(x_test)
        opt_threshold = plot_roc(y_test, y_prob, args.model)
        plot_pr(y_test, y_prob, args.model)
        plot_confusion_matrix(y_test, y_prob, opt_threshold, args.model)

if __name__ == '__main__':

    main()