# EngineAI Studio

This repository contains Fuad Ashraf's actual first-party source for a locally run engineering assistant and CAE workbench, including its Python command-line modules and browser worker.

## Start from the command line

Use Python 3.11 or newer in an isolated environment, install the packages listed in `requirements.txt`, then run `python engineai.py doctor` to see which optional engines and providers are available. A basic example command is:

```console
python engineai.py ask "cantilever tip deflection point load at free end" --P 1000 --L 1.0 --E 200e9 --I 8.333e-6
```

The public dependency list was made portable from a local environment lock; package versions for entries that originally referenced build-host files need to be pinned for a reproducible deployment.

## Project limits

This is prototype software. Solver and model-provider availability depends on the local environment and configuration. Outputs are engineering aids and are not certified design results; independently review them before relying on them.

The public source snapshot excludes local credentials, user-specific settings, machine inventories, personal documents, generated outputs, third-party dataset repositories, and private research corpora.
