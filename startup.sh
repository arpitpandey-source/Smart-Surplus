#!/bin/bash
# Startup script for Azure App Service (Linux).
# Set this as the "Startup command" in App Service → Configuration → General settings.
#
# Azure App Service already activates the virtual environment automatically for
# Python deployments.  This script assumes 'gunicorn' is on PATH after
# 'pip install -r requirements.txt' has been run during the build step.
#
# IMPORTANT: Do NOT run 'flask init-azure-db' here — that is a one-time manual
# step you run yourself before the first deployment (see docs/AZURE_DEPLOYMENT.md).

gunicorn --config gunicorn.conf.py app:app

