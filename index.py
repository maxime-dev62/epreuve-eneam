import sys
import os

# On ajoute le dossier 'api' au chemin de recherche de Python
sys.path.append(os.path.join(os.path.dirname(__file__), 'api'))

# On importe ton application Flask depuis le dossier api
from app import app
