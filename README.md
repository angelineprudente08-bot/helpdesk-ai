# HelpDesk AI

An IT support ticket system that uses machine learning to suggest the support queue and priority of each new ticket. Course project for CPSOFT1l-3 (Project ML).

Built with Python, Flask, SQLite, Bootstrap, and scikit-learn (TF-IDF + Logistic Regression).

## Roles
- **Requester**: submits tickets and talks with the technician
- **Technician**: confirms or corrects the suggested category and priority, and resolves tickets
- **Administrator**: assigns tickets, manages users and categories, and views reports

## How to run it on your laptop (Windows)
1. Install Python 3.12 or newer from https://www.python.org and tick **Add Python to PATH**.
2. Click the green **Code** button on this page, choose **Download ZIP**, and unzip it.
3. Open PowerShell inside the unzipped folder and run these one at a time:

        py -m venv venv
        venv\Scripts\activate
        python -m pip install -r requirements.txt
        python db.py
        python seed_categories.py
        python create_admin.py
        python run.py

4. Open http://127.0.0.1:5000 in your browser and log in with the admin account you just created.

`db.py` creates an empty database, `seed_categories.py` adds the 10 support queues, and `create_admin.py` makes the first administrator. To make a technician, register a normal account, then change its role on the admin's **Users** page.

The next time, you only need `venv\Scripts\activate` and `python run.py`.

## Retraining the model (optional)
The trained model is already in `models/`. To train it again from the dataset:

    python ml\prepare_data.py
    python ml\train.py

## Dataset
Tobias Bück, Customer IT Support Ticket Dataset (Kaggle), file `dataset-tickets-multi-lang-4-20k.csv`, English rows only.
