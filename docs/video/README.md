# Vidéos de démonstration — trois scénarios

Ce dossier contient le conducteur et les sous-titres de trois vidéos de **5 minutes** :

1. `01-nominal.srt` — une journée type réussie ;
2. `02-qualite-degradee.srt` — un run vert mais une qualité métier dégradée ;
3. `03-contrat-casse.srt` — un incident bloquant détecté dès l’ingestion.

Les fichiers SRT sont aussi le texte de voix off. Ils sont cadencés par tranches de
dix secondes : parle naturellement, sans chercher à remplir chaque silence. Dans
DaVinci Resolve, CapCut ou Premiere, importe le fichier SRT correspondant puis
enregistre la voix en suivant le texte.

## Préparation commune

Avant l’enregistrement, ouvre Streamlit, Grafana, Marquez et Mailpit. Démarre sur
une plateforme propre avec **5 · Réinitialisation**, puis joue **1 · Nominal** au
moins une fois. Masque les mots de passe, les favoris et les notifications du
navigateur. Filme en 1920×1080, à 30 fps, avec le curseur visible.

## Découpage écran

| Vidéo | Écrans à montrer | Fin attendue |
|---|---|---|
| Nominal | Streamlit → Mailpit → Marquez → Streamlit | run vert, 0 rejet, data mart publié |
| Qualité dégradée | Streamlit → Grafana/Mailpit → Streamlit | run vert, quarantaine > 5 %, alerte |
| Contrat cassé | Streamlit → détail des logs → Mailpit/Grafana | run rouge, 100 rejets, alertes |

Si une exécution est plus longue que prévu, garde une capture ou un plan fixe de
l’écran concerné ; le propos reste valable. Évite d’accélérer la voix : l’objectif
est une démonstration compréhensible, pas de montrer chaque seconde de calcul.

Les captures déjà présentes dans `docs/images/` peuvent servir de plans de coupe :
`01`, `02`, `03` et `03b` pour le nominal ; `04` pour la qualité ; `05`, `06` et
`07` pour le contrat cassé.

## Rendre les MP4

Le script `render-videos.ps1` construit des MP4 1080p de 5 minutes : capture écran,
carton de titre, sous-titres incrustés et piste audio silencieuse. Depuis la racine
du dépôt :

```powershell
powershell -ExecutionPolicy Bypass -File docs/video/render-videos.ps1
```

Les fichiers sont créés dans `docs/video/output/`. Ajoute ensuite ta voix, ou une
voix IA, dans le logiciel de montage ; les sous-titres et le minutage n’ont pas à
être modifiés.
