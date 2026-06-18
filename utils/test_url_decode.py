#!/usr/bin/env python3
"""
Test de décodage d'URL iTunes
"""

import urllib.parse
import os

def test_url_decoding():
    # URL exemple tirée de votre bibliothèque
    url = "file://localhost/mnt/MyBook/Musiques/Music/C'est%20l'ap%C3%A9ro%20(Parodie%20Luis%20Fonsi)/Audios/C'est%20l'ap%C3%A9ro%20(Parodie%20Luis%20Fonsi)%20-.m4a"
    
    print("🔍 TEST DE DÉCODAGE D'URL")
    print("="*50)
    print(f"URL originale: {url}")
    
    # Décoder l'URL
    decoded = urllib.parse.unquote(url)
    print(f"URL décodée:   {decoded}")
    
    # Extraire le chemin de fichier
    if decoded.startswith('file://localhost'):
        file_path = decoded.replace('file://localhost', '')
    elif decoded.startswith('file://'):
        file_path = decoded.replace('file://', '')
    else:
        file_path = decoded
    
    print(f"Chemin:        {file_path}")
    
    # Vérifier l'existence
    exists = os.path.exists(file_path)
    print(f"Existe:        {exists}")
    
    if not exists:
        # Tester les dossiers parents
        path_parts = file_path.split('/')
        for i in range(1, len(path_parts)):
            partial_path = '/'.join(path_parts[:i+1])
            if os.path.exists(partial_path):
                print(f"Existe jusqu'à: {partial_path}")
            else:
                print(f"N'existe pas:   {partial_path}")
                break
    
    return exists

def test_directory_structure():
    print(f"\n📁 TEST DE LA STRUCTURE")
    print("="*50)
    
    base_paths = [
        "/mnt/MyBook/",
        "/mnt/MyBook/Musiques/",
        "/mnt/MyBook/Musiques/Music/",
    ]
    
    for path in base_paths:
        exists = os.path.exists(path)
        print(f"{path:30} : {'✅' if exists else '❌'}")
        
        if exists and os.path.isdir(path):
            try:
                contents = os.listdir(path)[:5]  # Premiers 5 éléments
                print(f"   Contient: {contents}")
            except PermissionError:
                print(f"   Permission refusée")

if __name__ == "__main__":
    test_url_decoding()
    test_directory_structure()