#!/bin/bash
# Test des nouvelles génération de posters
# Usage: ./test_posters.sh [style_name]
# Exemple: ./test_posters.sh elegant

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STYLES_DIR="$SCRIPT_DIR"

# Styles disponibles
AVAILABLE_STYLES=(
    "elegant"
    "modern"
    "minimal"
    "vibrant"
    "synthwave"
    "luxe"
    "default"
)

# Couleurs de sortie
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}══════════════════════════════════════════${NC}"
    echo -e "${BLUE}🎨  Test de Génération de Posters${NC}"
    echo -e "${BLUE}══════════════════════════════════════════${NC}\n"
}

print_styles() {
    echo -e "${YELLOW}Styles disponibles:${NC}"
    for i in "${!AVAILABLE_STYLES[@]}"; do
        echo "  $((i+1)). ${AVAILABLE_STYLES[$i]}"
    done
    echo ""
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

main() {
    print_header
    
    # Vérifier Python3
    if ! command -v python3 &> /dev/null; then
        print_error "Python3 n'est pas installé"
        exit 1
    fi
    
    # Vérifier le script de test
    if [ ! -f "$STYLES_DIR/test_poster_generation.py" ]; then
        print_error "test_poster_generation.py non trouvé"
        exit 1
    fi
    
    # Sélectionner le style
    if [ -n "$1" ]; then
        STYLE="$1"
    else
        print_styles
        read -p "Choisir un style (1-7): " choice
        if [ "$choice" -ge 1 ] && [ "$choice" -le 7 ]; then
            STYLE="${AVAILABLE_STYLES[$((choice-1))]}"
        else
            print_error "Choix invalide"
            exit 1
        fi
    fi
    
    STYLE_FILE="poster_style.${STYLE}.json"
    
    # Vérifier que le fichier existe
    if [ ! -f "$STYLES_DIR/$STYLE_FILE" ]; then
        print_error "Style '$STYLE' non trouvé"
        exit 1
    fi
    
    print_info "Test avec le style: $STYLE"
    print_info "Fichier: $STYLE_FILE\n"
    
    # Générer les posters de test
    export PLEX_POSTER_STYLE_CONFIG="$STYLES_DIR/$STYLE_FILE"
    python3 "$STYLES_DIR/test_poster_generation.py"
    
    if [ $? -eq 0 ]; then
        print_success "Posters générés avec succès!"
        print_info "Dossier: $STYLES_DIR/poster_samples/"
        
        # Afficher les infos du fichier
        SAMPLE_FILE="$STYLES_DIR/poster_samples/poster_style.${STYLE}.png"
        if [ -f "$SAMPLE_FILE" ]; then
            SIZE=$(du -h "$SAMPLE_FILE" | cut -f1)
            print_info "Fichier d'exemple: $SAMPLE_FILE ($SIZE)"
        fi
    else
        print_error "Erreur lors de la génération"
        exit 1
    fi
}

# Lancer le test
main "$@"
