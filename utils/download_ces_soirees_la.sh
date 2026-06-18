#!/bin/bash
# Télécharge les morceaux de "Ces soirées-là !" en MP3 via yt-dlp (recherche YouTube)
# Destination : /media/paulceline/MUSIC/Ces soirées-là !

DEST="/media/paulceline/MUSIC/Ces soirées-là !"
mkdir -p "$DEST"

tracks=(
  "01|Lady - Easy Love"
  "02|Ultra Naté - Free"
  "03|Galleon - So I Begin"
  "04|Anastacia - I'm Outta Love"
  "05|Black & White Brothers - Put Your Hands Up"
  "06|Soul Searcher - Can't Get Enough"
  "07|Cleptomaniacs - All I Do"
  "08|Bryan Chambers - Sexual"
  "09|Eiffel 65 - Blue Da Ba Dee"
  "10|Stanford University Talisman - So Good So Right"
  "11|LaBelle - Lady Marmalade"
  "12|The Jacksons - Blame It on the Boogie"
  "13|Jenny Mac Kay - It's Raining Men"
  "14|Patrick Hernandez - Born to Be Alive"
  "15|Kool & the Gang - Celebration"
  "16|Earth Wind & Fire - September"
  "17|Billy Paul - Your Song"
  "18|Hermes House Band - I Will Survive"
  "19|Do Brazil 98"
  "20|Village People - YMCA"
  "21|Paul Johnson - Get Get Down"
  "22|Gypsy Men - Babarabatiri"
  "23|Jamalak - Papa Chico"
  "24|Janeiro Verde - Brasilia Carnaval"
  "25|Wes - Alane"
  "26|Nomads - Yakalélo"
  "27|Havana Delirio - Carnavalera"
  "28|King África - La Bomba"
  "29|Cubaila - La Charanga"
  "30|Bellini - Samba de Janeiro"
  "31|Yannick - Ces soirées-là"
  "32|Émile et Images - Jusqu'au bout de la nuit"
  "33|Up and Down"
  "34|Bébé Charli - KKOQQ"
  "35|Wazoo - La Manivelle"
  "36|Sawt El Atlas - Ne me jugez pas"
  "37|Sharon Williams - Life Is So Strong"
  "38|Ofasia - Sate Fan"
  "39|Doing Time - I Was a Ye-Ye Girl"
  "40|Bob & Vanessa - Le Waka"
  "41|Thierry Hazard - Le Jerk"
)

total=${#tracks[@]}
ok=0
fail=0

for entry in "${tracks[@]}"; do
  num="${entry%%|*}"
  search="${entry#*|}"
  outfile="$DEST/${num} - ${search}.%(ext)s"

  echo "=== [$num/$total] $search ==="

  yt-dlp \
    --default-search "ytsearch1" \
    --extract-audio \
    --audio-format mp3 \
    --audio-quality 0 \
    --embed-thumbnail \
    --add-metadata \
    --no-playlist \
    --output "$outfile" \
    "$search" 2>&1

  if [[ $? -eq 0 ]]; then
    ((ok++))
    echo "  -> OK"
  else
    ((fail++))
    echo "  -> ÉCHEC"
  fi
  echo
done

echo "=== Terminé : $ok réussis, $fail échoués sur $total ==="
