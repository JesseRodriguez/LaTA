#!/usr/bin/env bash

# Install Python 3 and pip
apt-get update
apt-get install -y python3 python3-pip

# Install Python dependencies
pip3 install -r /autograder/source/requirements.txt
