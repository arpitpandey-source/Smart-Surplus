# Gunicorn configuration for SmartSurplus on Azure App Service.
# Azure App Service Linux containers expose port 8000 by default.
#
# Docs: https://docs.gunicorn.org/en/stable/configure.html

bind = "0.0.0.0:8000"

# 2 workers is a safe baseline for the Azure App Service Free / Basic tiers.
# Rule of thumb: 2 × <number of CPU cores> + 1.
workers = 2
worker_class = "sync"

# Seconds a worker can spend on a request before being killed and restarted.
timeout = 120

# Log to stdout/stderr so Azure Log Stream captures everything automatically.
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Reload workers gracefully when the source code changes (dev-friendly;
# disable in pure production if you don't redeploy via restart).
reload = False

