#!/bin/bash
# install_domains.sh -- activate the remaining physics domains ONE AT A TIME.
# Run a section, then `python agent.py tools` to confirm it shows active.
# Deliberately not automated end-to-end: each backend is big, and a failed
# half-install is worse than a missing one.

set -e

echo "Pick a section and run its lines manually. Sections:"
echo "  1 CFD (OpenFOAM)   2 CALPHAD (pycalphad)   3 EM (Elmer)"
echo "  4 Circuits (ngspice)   5 Optimization (OpenMDAO)   6 Multibody"
exit 0

# ---- 1. CFD -- OpenFOAM (foundation build) --------------------------------
# NOTE: check https://openfoam.org/download/ for the current version number.
# curl -s https://dl.openfoam.org/gpg.key | sudo tee /etc/apt/trusted.gpg.d/openfoam.asc
# sudo add-apt-repository http://dl.openfoam.org/ubuntu
# sudo apt update
# sudo apt install -y openfoam12
# echo 'source /opt/openfoam12/etc/bashrc' >> ~/.bashrc

# ---- 2. Materials thermodynamics -- pyCALPHAD (DED printer critical path) --
# conda activate eng
# pip install pycalphad
# (TDB databases: start with the free COST507 Al database for 7075-class work)

# ---- 3. Electromagnetics -- Elmer (CRFCE motor domain) ---------------------
# sudo add-apt-repository ppa:elmer-csc-ubuntu/elmer-csc-ppa
# sudo apt update
# sudo apt install -y elmerfem-csc

# ---- 4. Circuits -- ngspice ------------------------------------------------
# sudo apt install -y ngspice

# ---- 5. Optimization -- OpenMDAO (scipy already active for local opt) ------
# conda activate eng
# pip install openmdao

# ---- 6. Multibody -- Project Chrono ---------------------------------------
# conda install -c projectchrono pychrono
# (check python version compatibility; may need its own env)

# ---- 7. Local reflex layer -- Ollama (log parsing, embeddings) -------------
# curl -fsSL https://ollama.com/install.sh | sh
# ollama pull qwen2.5:7b
