#!/bin/bash
searches=(
  "Lady Easy Love"
  "Ultra Naté Free"
  "Galleon So I Begin"
  "Anastacia I'm Outta Love"
  "Black & White Brothers Put Your Hands Up"
  "Soul Searcher Can't Get Enough"
  "Cleptomaniacs All I Do"
  "Bryan Chambers Sexual"
  "Eiffel 65 Blue Da Ba Dee"
  "Stanford University Talisman So Good So Right"
  "LaBelle Lady Marmalade"
  "The Jacksons Blame It on the Boogie"
  "Jenny Mac Kay It's Raining Men"
  "Patrick Hernandez Born to Be Alive"
  "Kool & the Gang Celebration"
  "Earth Wind & Fire September"
  "Billy Paul Your Song"
  "Hermes House Band I Will Survive"
  "Do Brazil 98"
  "Village People YMCA"
  "Paul Johnson Get Get Down"
  "Gypsy Men Babarabatiri"
  "Jamalak Papa Chico"
  "Janeiro Verde Brasilia Carnaval"
  "Wes Alane"
  "Nomads Yakalélo"
  "Havana Delirio Carnavalera"
  "King África La Bomba"
  "Cubaila La Charanga"
  "Bellini Samba de Janeiro"
  "Yannick Ces soirées-là"
  "Émile et Images Jusqu'au bout de la nuit"
  "Up and Down dance 90s"
  "Bébé Charli KKOQQ"
  "Wazoo La Manivelle"
  "Sawt El Atlas Ne me jugez pas"
  "Sharon Williams Life Is So Strong"
  "Ofasia Sate Fan"
  "Doing Time I Was a Ye-Ye Girl"
  "Bob & Vanessa Le Waka"
  "Thierry Hazard Le Jerk"
)

out="ces_soirees_la_jdownloader.txt"
> "$out"
total=${#searches[@]}
i=0

for s in "${searches[@]}"; do
  ((i++))
  echo "[$i/$total] $s ..."
  url=$(yt-dlp --default-search "ytsearch1" --get-url --no-playlist -f bestaudio "$s" 2>/dev/null | head -1)
  video_url=$(yt-dlp --default-search "ytsearch1" --get-id --no-playlist "$s" 2>/dev/null | head -1)
  if [[ -n "$video_url" ]]; then
    echo "https://www.youtube.com/watch?v=$video_url" >> "$out"
    echo "  -> https://www.youtube.com/watch?v=$video_url"
  else
    echo "  -> INTROUVABLE"
    echo "# INTROUVABLE: $s" >> "$out"
  fi
done

echo ""
echo "=== Terminé ! $(grep -c 'youtube.com' "$out") liens trouvés dans $out ==="
