import json
import os
import time

import folium
import geopandas as gpd
import pandas as pd
import requests
from collections import Counter
from folium.plugins import MarkerCluster
from shapely.geometry import LineString, MultiLineString, Point, box

ZONE = "46.85,6.45,47.15,7.00"  # sud lat, ouest lon, nord lat, est lon — région Le Locle/Neuchâtel/La Chaux-de-Fonds

REQUETE_PARKINGS = f"""
[out:json][timeout:25];
(
  node["amenity"="parking"]({ZONE});
);
out;
"""

REQUETE_ARRETS = f"""
[out:json][timeout:30];
(
  node["highway"="bus_stop"]({ZONE});
  node["railway"="station"]({ZONE});
  node["railway"="halt"]({ZONE});
);
out;
"""

REQUETE_ITINERAIRES = f"""
[out:json][timeout:60];
relation["route"="hiking"]({ZONE});
(._;>;);
out geom;
"""

REQUETE_OSM_DIFFICILES = f"""
[out:json][timeout:60];
(
  way["highway"~"path|footway"]({ZONE});
);
out geom;
"""

COULEURS_DIFFICULTE = {
    "hiking": "green",
    "mountain_hiking": "orange",
    "demanding_mountain_hiking": "red",
    "alpine_hiking": "black",
    "non_classifie": "gray"
}

NOMS_SIMPLES_DIFFICULTE = {
    "hiking": "Facile",
    "mountain_hiking": "Moyen",
    "demanding_mountain_hiking": "Difficile",
    "alpine_hiking": "Très difficile",
    "non_classifie": "Non classifié"
}

CORRESPONDANCE_DIFFICULTE_SWISSTOPO = {
    "Wanderweg": "hiking",
    "Bergwanderweg": "mountain_hiking"
}

DISTANCE_MAX_ARRET = 500  # mètres : distance sous laquelle un itinéraire est considéré accessible en transport public


def charger_template(nom_fichier):
    return open(os.path.join("templates", nom_fichier), encoding="utf-8").read()


def charger_json_avec_cache(chemin_cache, fonction_recuperation):
    if os.path.exists(chemin_cache):
        return json.load(open(chemin_cache))
    donnees = fonction_recuperation()
    json.dump(donnees, open(chemin_cache, "w"))
    return donnees


def interroger_overpass(requete):
    reponse = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": requete},
        headers={"User-Agent": "projet-apprentissage-python/1.0"},
        timeout=90
    )
    reponse.raise_for_status()
    return reponse.json()


SEUIL_BRUIT_ALTITUDE = 2  # mètres : en dessous, une variation d'altitude est considérée comme du bruit de mesure, pas une vraie montée/descente

def calculer_denivele(altitudes, seuil=SEUIL_BRUIT_ALTITUDE):
    if len(altitudes) == 0:
        return 0, 0
    positif = 0
    negatif = 0
    altitude_reference = altitudes[0]
    for altitude in altitudes[1:]:
        difference = altitude - altitude_reference
        if abs(difference) >= seuil:
            if difference > 0:
                positif = positif + difference
            else:
                negatif = negatif + abs(difference)
            altitude_reference = altitude
    return positif, negatif


def recuperer_elevations(points, taille_lot=5000):
    if len(points) == 0:
        return []
    tous_resultats = []
    for i in range(0, len(points), taille_lot):
        lot = points[i:i + taille_lot]
        reponse = requests.post(
            "https://api.open-elevation.com/api/v1/lookup",
            json={"locations": lot},
            timeout=120
        )
        reponse.raise_for_status()
        tous_resultats.extend(reponse.json()["results"])
    return tous_resultats


# La plupart des parkings OSM n'ont pas de nom : on retrouve la localité/rue la plus proche par géolocalisation inverse (Nominatim).
def geocoder_noms_parkings(donnees_parkings):
    def recuperer_noms_bruts():
        noms = {}
        for parking in donnees_parkings["elements"]:
            id_parking = str(parking["id"])
            tags_parking = parking.get("tags", {})
            if tags_parking.get("name"):
                nom_brut = tags_parking["name"]
                noms[id_parking] = nom_brut if nom_brut.lower().startswith("parking") else f"Parking {nom_brut}"
            elif tags_parking.get("description"):
                nom_brut = tags_parking["description"]
                noms[id_parking] = nom_brut if nom_brut.lower().startswith("parking") else f"Parking {nom_brut}"
            else:
                reponse_geocode = requests.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={"format": "json", "lat": parking["lat"], "lon": parking["lon"], "zoom": 16},
                    headers={"User-Agent": "projet-apprentissage-python/1.0"},
                    timeout=15
                )
                reponse_geocode.raise_for_status()
                adresse = reponse_geocode.json().get("address", {})
                rue = adresse.get("road")
                localite = adresse.get("village") or adresse.get("town") or adresse.get("hamlet") or adresse.get("suburb")
                if rue and localite:
                    noms[id_parking] = f"Parking — {rue}, {localite}"
                elif rue or localite:
                    noms[id_parking] = f"Parking — {rue or localite}"
                else:
                    noms[id_parking] = f"Parking {id_parking}"
                time.sleep(1)
        return noms

    noms_parkings = charger_json_avec_cache("noms_parkings.json", recuperer_noms_bruts)

    # Deux parkings peuvent partager le même nom de localité : on numérote pour les distinguer dans la recherche.
    compte_noms_parkings = {}
    noms_parkings_uniques = {}
    for parking in donnees_parkings["elements"]:
        id_parking = str(parking["id"])
        nom_de_base = noms_parkings[id_parking]
        compte_noms_parkings[nom_de_base] = compte_noms_parkings.get(nom_de_base, 0) + 1
        if compte_noms_parkings[nom_de_base] > 1:
            noms_parkings_uniques[id_parking] = f"{nom_de_base} ({compte_noms_parkings[nom_de_base]})"
        else:
            noms_parkings_uniques[id_parking] = nom_de_base
    return noms_parkings_uniques


def construire_itineraires_osm(donnees_itineraires):
    # Un "way" est un segment ; une "relation" est un itinéraire complet qui référence plusieurs segments.
    segments_par_id = {}
    for element in donnees_itineraires["elements"]:
        if element["type"] == "way":
            segments_par_id[element["id"]] = [(point["lon"], point["lat"]) for point in element["geometry"]]

    def recuperer_elevations_itineraires():
        tous_points = []
        for way_id in segments_par_id:
            for lon, lat in segments_par_id[way_id]:
                tous_points.append({"latitude": lat, "longitude": lon})
        return {"results": recuperer_elevations(tous_points)}

    resultats_elevations = charger_json_avec_cache("elevations_itineraires.json", recuperer_elevations_itineraires)["results"]
    toutes_altitudes = [r["elevation"] for r in resultats_elevations]

    altitudes_par_way = {}
    curseur = 0
    for way_id in segments_par_id:
        nb_points = len(segments_par_id[way_id])
        altitudes_par_way[way_id] = toutes_altitudes[curseur:curseur + nb_points]
        curseur = curseur + nb_points

    lignes_itineraires = []
    noms_itineraires = []
    denivele_positif_itineraires = []
    denivele_negatif_itineraires = []

    for element in donnees_itineraires["elements"]:
        if element["type"] == "relation" and element["tags"].get("network") == "lwn":
            segments_de_cet_itineraire = []
            altitudes_de_cet_itineraire = []
            for membre in element["members"]:
                if membre["type"] == "way" and membre["ref"] in segments_par_id:
                    segments_de_cet_itineraire.append(segments_par_id[membre["ref"]])
                    altitudes_de_cet_itineraire.extend(altitudes_par_way[membre["ref"]])
            if len(segments_de_cet_itineraire) > 0:
                lignes_itineraires.append(MultiLineString(segments_de_cet_itineraire))
                noms_itineraires.append(element["tags"].get("name", "Itinéraire sans nom"))
                positif, negatif = calculer_denivele(altitudes_de_cet_itineraire)
                denivele_positif_itineraires.append(positif)
                denivele_negatif_itineraires.append(negatif)

    gdf_itineraires = gpd.GeoDataFrame({
        "nom": noms_itineraires,
        "geometry": lignes_itineraires,
        "denivele_positif": denivele_positif_itineraires,
        "denivele_negatif": denivele_negatif_itineraires
    }, crs="EPSG:4326")
    gdf_itineraires_2056 = gdf_itineraires.to_crs("EPSG:2056")
    gdf_itineraires["longueur_km"] = gdf_itineraires_2056.geometry.length / 1000
    return gdf_itineraires, gdf_itineraires_2056


# Arrêts de transport public (bus/train), pour voir quels itinéraires sont accessibles sans voiture.
def construire_arrets_transport(donnees_arrets):
    noms_arrets = []
    types_arrets = []
    points_arrets = []

    for element in donnees_arrets["elements"]:
        points_arrets.append(Point(element["lon"], element["lat"]))
        noms_arrets.append(element["tags"].get("uic_name", element["tags"].get("name", "Arrêt sans nom")))
        types_arrets.append("train" if "railway" in element["tags"] else "bus")

    gdf_arrets = gpd.GeoDataFrame({"nom": noms_arrets, "type": types_arrets, "geometry": points_arrets}, crs="EPSG:4326")
    gdf_arrets_2056 = gdf_arrets.to_crs("EPSG:2056")
    return gdf_arrets, gdf_arrets_2056


# Un itinéraire est "accessible en transport public" si un arrêt est à moins de 500m de son départ OU de son arrivée (un "buffer" de proximité).
def calculer_accessibilite_transport(gdf_itineraires_2056, gdf_arrets_2056):
    accessible_transport = []
    for index, row in gdf_itineraires_2056.iterrows():
        premier_segment = list(row.geometry.geoms)[0]
        dernier_segment = list(row.geometry.geoms)[-1]
        point_depart = Point(premier_segment.coords[0])
        point_arrivee = Point(dernier_segment.coords[-1])

        distance_min_depart = gdf_arrets_2056.geometry.distance(point_depart).min()
        distance_min_arrivee = gdf_arrets_2056.geometry.distance(point_arrivee).min()

        accessible_transport.append(distance_min_depart < DISTANCE_MAX_ARRET or distance_min_arrivee < DISTANCE_MAX_ARRET)
    return accessible_transport


# Source officielle swisstopo (swissTLM3D Wanderwege) : réseau de sentiers curé par l'État, avec l'altitude déjà incluse dans la géométrie (coordonnée Z).
def charger_sentiers_officiels():
    zone_bbox = gpd.GeoSeries([box(6.45, 46.85, 7.00, 47.15)], crs="EPSG:4326")
    gdf_sentiers_bruts = gpd.read_file("sentiers_officiels_extraits/SWISSTLM3D_WANDERWEGE.gpkg", bbox=zone_bbox)

    lignes = []
    difficultes = []
    noms = []
    denivele_positif = []
    denivele_negatif = []

    for index, row in gdf_sentiers_bruts.iterrows():
        coords_xy = [(x, y) for x, y, z in row.geometry.coords]
        lignes.append(LineString(coords_xy))
        difficultes.append(CORRESPONDANCE_DIFFICULTE_SWISSTOPO.get(row["wanderwege"], "non_classifie"))
        noms.append(row["name"] if pd.notna(row["name"]) else "Sentier sans nom")

        altitudes = [z for x, y, z in row.geometry.coords]
        positif, negatif = calculer_denivele(altitudes)
        denivele_positif.append(positif)
        denivele_negatif.append(negatif)

    return lignes, difficultes, noms, denivele_positif, denivele_negatif


# On retire les segments isolés (leurs 2 extrémités ne touchent aucun autre segment) : souvent des petits chemins sans lien avec le réseau de randonnée.
def filtrer_et_connecter_segments(lignes, difficultes, noms, denivele_positif, denivele_negatif):
    compte_extremites = Counter()
    for ligne in lignes:
        compte_extremites[ligne.coords[0]] += 1
        compte_extremites[ligne.coords[-1]] += 1

    # Un identifiant unique par point de jonction, pour savoir ensuite si un point est un simple passage (2 segments) ou un vrai croisement (3+).
    id_par_coordonnee = {}

    def id_du_noeud(coordonnee):
        if coordonnee not in id_par_coordonnee:
            id_par_coordonnee[coordonnee] = len(id_par_coordonnee)
        return id_par_coordonnee[coordonnee]

    lignes_connectees = []
    difficultes_connectees = []
    noms_connectes = []
    denivele_positif_connecte = []
    denivele_negatif_connecte = []
    noeuds_debut_connectes = []
    noeuds_fin_connectes = []

    for i in range(len(lignes)):
        debut_partage = compte_extremites[lignes[i].coords[0]] > 1
        fin_partagee = compte_extremites[lignes[i].coords[-1]] > 1
        if debut_partage or fin_partagee:
            lignes_connectees.append(lignes[i])
            difficultes_connectees.append(difficultes[i])
            noms_connectes.append(noms[i])
            denivele_positif_connecte.append(denivele_positif[i])
            denivele_negatif_connecte.append(denivele_negatif[i])
            noeuds_debut_connectes.append(id_du_noeud(lignes[i].coords[0]))
            noeuds_fin_connectes.append(id_du_noeud(lignes[i].coords[-1]))

    print(f"{len(lignes_connectees)}/{len(lignes)} segments gardés (connectés au réseau)")

    return {
        "id_par_coordonnee": id_par_coordonnee,
        "lignes": lignes_connectees,
        "difficultes": difficultes_connectees,
        "noms": noms_connectes,
        "denivele_positif": denivele_positif_connecte,
        "denivele_negatif": denivele_negatif_connecte,
        "noeuds_debut": noeuds_debut_connectes,
        "noeuds_fin": noeuds_fin_connectes
    }


# Pour chaque jonction du réseau, on retient l'arrêt de transport public le plus proche : ça permettra de suggérer un point de départ accessible sans voiture.
def associer_arrets_aux_noeuds(id_par_coordonnee, gdf_arrets_2056):
    infos_noeuds = {}
    for coordonnee, id_noeud in id_par_coordonnee.items():
        point_noeud = Point(coordonnee)
        distances_arrets = gdf_arrets_2056.geometry.distance(point_noeud)
        index_plus_proche = distances_arrets.idxmin()
        infos_noeuds[id_noeud] = {
            "nom_arret": gdf_arrets_2056.loc[index_plus_proche, "nom"],
            "distance_m": round(distances_arrets.loc[index_plus_proche], 0)
        }
    return infos_noeuds


# swisstopo ne distingue pas les sentiers difficiles/alpins dans cette région, mais OpenStreetMap (sac_scale) identifie certains passages réels.
# On recolore les segments swisstopo concernés avec le niveau OSM, plutôt que de dessiner des fragments à part : le réseau reste topologiquement complet, sans coupure.
def reclasser_segments_difficiles(segments_connectes):
    donnees_osm_difficiles = charger_json_avec_cache("sentiers_bruts.json", lambda: interroger_overpass(REQUETE_OSM_DIFFICILES))

    niveaux_a_recuperer = ["demanding_mountain_hiking", "alpine_hiking"]
    elements_difficiles = [e for e in donnees_osm_difficiles["elements"] if e["tags"].get("sac_scale") in niveaux_a_recuperer]

    zones_difficiles = []
    for element in elements_difficiles:
        ligne_osm_4326 = LineString([(p["lon"], p["lat"]) for p in element["geometry"]])
        ligne_osm_2056 = gpd.GeoSeries([ligne_osm_4326], crs="EPSG:4326").to_crs("EPSG:2056").iloc[0]
        zones_difficiles.append({"zone": ligne_osm_2056.buffer(15), "niveau": element["tags"]["sac_scale"], "element": element, "trouve_dans_swisstopo": False})

    lignes_connectees = segments_connectes["lignes"]
    difficultes_connectees = segments_connectes["difficultes"]

    # Un segment swisstopo est reclassé si une bonne partie de sa longueur (>30%) passe dans une zone difficile identifiée sur OSM.
    for i in range(len(lignes_connectees)):
        for zone_info in zones_difficiles:
            if lignes_connectees[i].intersects(zone_info["zone"]):
                intersection = lignes_connectees[i].intersection(zone_info["zone"])
                if intersection.length / lignes_connectees[i].length > 0.3:
                    difficultes_connectees[i] = zone_info["niveau"]
                    zone_info["trouve_dans_swisstopo"] = True
                    break

    # Certains sentiers dangereux d'OSM ne font pas partie du réseau officiel swisstopo (chemin non classé, sentier non repris) :
    # on les garde en mémoire pour les dessiner quand même, sinon ils disparaîtraient silencieusement de la carte.
    elements_difficiles_hors_reseau = [zone_info["element"] for zone_info in zones_difficiles if not zone_info["trouve_dans_swisstopo"]]
    print(f"{len(zones_difficiles) - len(elements_difficiles_hors_reseau)}/{len(zones_difficiles)} passages difficiles OSM rattachés au réseau swisstopo, {len(elements_difficiles_hors_reseau)} ajoutés à part")

    return donnees_osm_difficiles, elements_difficiles, elements_difficiles_hors_reseau


# Les passages difficiles/alpins d'OSM absents du réseau swisstopo sont ajoutés à part, avec leurs chemins OSM voisins directs
# (couleur neutre) accrochés à chaque extrémité, pour que la ligne ne s'arrête plus dans le vide.
def construire_segments_hors_reseau(donnees_osm_difficiles, elements_difficiles, elements_difficiles_hors_reseau):
    toutes_les_voies_osm = [e for e in donnees_osm_difficiles["elements"] if e["type"] == "way"]
    noeud_vers_voie = {}
    for voie in toutes_les_voies_osm:
        for id_noeud in [voie["nodes"][0], voie["nodes"][-1]]:
            noeud_vers_voie.setdefault(id_noeud, []).append(voie)

    def trouver_voisin(voie, id_noeud):
        for candidate in noeud_vers_voie.get(id_noeud, []):
            if candidate["id"] != voie["id"]:
                return candidate
        return None

    ids_deja_traites = set(e["id"] for e in elements_difficiles)
    identifiants_dessines = set()

    elements_a_dessiner = []
    for element in elements_difficiles_hors_reseau:
        elements_a_dessiner.append({"element": element, "niveau": element["tags"]["sac_scale"], "connecteur": False})
        identifiants_dessines.add(element["id"])
        for id_noeud in [element["nodes"][0], element["nodes"][-1]]:
            voisin = trouver_voisin(element, id_noeud)
            if voisin is not None and voisin["id"] not in ids_deja_traites and voisin["id"] not in identifiants_dessines:
                niveau_voisin = voisin["tags"].get("sac_scale", "non_classifie")
                if niveau_voisin not in COULEURS_DIFFICULTE:
                    niveau_voisin = "non_classifie"
                elements_a_dessiner.append({"element": voisin, "niveau": niveau_voisin, "connecteur": True})
                identifiants_dessines.add(voisin["id"])

    points_altitude = []
    for item in elements_a_dessiner:
        for point in item["element"]["geometry"]:
            points_altitude.append({"latitude": point["lat"], "longitude": point["lon"]})

    toutes_altitudes = [r["elevation"] for r in recuperer_elevations(points_altitude)]

    segments = []
    curseur = 0
    for item in elements_a_dessiner:
        element = item["element"]
        niveau = item["niveau"]
        nb_points = len(element["geometry"])
        altitudes_segment = toutes_altitudes[curseur:curseur + nb_points]
        curseur = curseur + nb_points

        positif, negatif = calculer_denivele(altitudes_segment)

        ligne_2056 = gpd.GeoSeries([LineString([(p["lon"], p["lat"]) for p in element["geometry"]])], crs="EPSG:4326").to_crs("EPSG:2056")
        longueur_km = ligne_2056.length.iloc[0] / 1000

        segments.append({
            "coords": [(point["lat"], point["lon"]) for point in element["geometry"]],
            "niveau": niveau,
            "connecteur": item["connecteur"],
            "longueur_km": longueur_km,
            "denivele_positif": positif,
            "denivele_negatif": negatif
        })

    return segments


def construire_carte(gdf_sentiers, infos_noeuds, donnees_osm_difficiles, elements_difficiles, elements_difficiles_hors_reseau,
                      gdf_itineraires, donnees_parkings, noms_parkings_uniques, gdf_arrets, id_par_coordonnee):
    carte = folium.Map(
        location=[46.8, 8.2],
        zoom_start=8,
        min_zoom=7,
        max_zoom=17,
        max_bounds=True,
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr=(
            'Fond de carte : © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>, SRTM | '
            'style © <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA) | '
            'Sentiers : © <a href="https://www.swisstopo.admin.ch">swisstopo</a> | '
            'Parkings, arrêts et itinéraires : © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> (ODbL)'
        ),
        prefer_canvas=True
    )
    carte.fit_bounds([[45.8, 5.9], [47.85, 10.5]])

    groupe_difficulte = folium.FeatureGroup(name="Sentiers", show=True)
    infos_segments_js = []

    for index, row in gdf_sentiers.iterrows():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        couleur_difficulte = COULEURS_DIFFICULTE[row["difficulte"]]
        texte_popup = f"{row['nom']} — {NOMS_SIMPLES_DIFFICULTE[row['difficulte']]} — {row['longueur_km']:.1f}km — +{row['denivele_positif']:.0f}m / -{row['denivele_negatif']:.0f}m"
        ligne_segment = folium.PolyLine(locations=coords, color="purple", weight=3, opacity=1, popup=texte_popup)
        ligne_segment.add_to(groupe_difficulte)
        infos_segments_js.append({
            "nom_js": ligne_segment.get_name(),
            "couleur_origine": couleur_difficulte,
            "difficulte": row["difficulte"],
            "longueur_km": round(row["longueur_km"], 3),
            "denivele_positif": round(row["denivele_positif"], 1),
            "denivele_negatif": round(row["denivele_negatif"], 1),
            "noeud_debut": int(row["noeud_debut"]),
            "noeud_fin": int(row["noeud_fin"]),
            "arret_debut": infos_noeuds.get(int(row["noeud_debut"])),
            "arret_fin": infos_noeuds.get(int(row["noeud_fin"]))
        })

    segments_hors_reseau = construire_segments_hors_reseau(donnees_osm_difficiles, elements_difficiles, elements_difficiles_hors_reseau)
    for segment in segments_hors_reseau:
        if segment["connecteur"]:
            texte_popup = f"Sentier de liaison — {segment['longueur_km']:.1f}km — +{segment['denivele_positif']:.0f}m / -{segment['denivele_negatif']:.0f}m"
        else:
            texte_popup = f"{NOMS_SIMPLES_DIFFICULTE[segment['niveau']]} (hors réseau swisstopo) — {segment['longueur_km']:.1f}km — +{segment['denivele_positif']:.0f}m / -{segment['denivele_negatif']:.0f}m"

        ligne_segment = folium.PolyLine(locations=segment["coords"], color="purple", weight=3, opacity=1, popup=texte_popup)
        ligne_segment.add_to(groupe_difficulte)
        infos_segments_js.append({
            "nom_js": ligne_segment.get_name(),
            "couleur_origine": COULEURS_DIFFICULTE[segment["niveau"]],
            "difficulte": segment["niveau"],
            "longueur_km": round(segment["longueur_km"], 3),
            "denivele_positif": round(segment["denivele_positif"], 1),
            "denivele_negatif": round(segment["denivele_negatif"], 1),
            "noeud_debut": None,
            "noeud_fin": None,
            "arret_debut": None,
            "arret_fin": None
        })

    groupe_difficulte.add_to(carte)

    groupe_itineraires = folium.FeatureGroup(name="Itinéraires nommés (OSM)", show=False)
    infos_itineraires_js = []

    for index, row in gdf_itineraires.iterrows():
        badge_transport = " 🚌 accessible en transport public" if row["accessible_transport"] else ""
        texte_popup_itineraire = f"{row['nom']} — {row['longueur_km']:.1f}km — +{row['denivele_positif']:.0f}m / -{row['denivele_negatif']:.0f}m{badge_transport}"
        noms_js_segments = []
        for segment in row.geometry.geoms:
            coords_segment = [(lat, lon) for lon, lat in segment.coords]
            ligne = folium.PolyLine(locations=coords_segment, color="purple", weight=3, opacity=0.6, popup=texte_popup_itineraire)
            ligne.add_to(groupe_itineraires)
            noms_js_segments.append(ligne.get_name())
        infos_itineraires_js.append({
            "noms_js": noms_js_segments,
            "longueur_km": round(row["longueur_km"], 3),
            "denivele_positif": round(row["denivele_positif"], 1),
            "denivele_negatif": round(row["denivele_negatif"], 1)
        })

    groupe_itineraires.add_to(carte)

    groupe_parkings = MarkerCluster(name="parkings", show=False).add_to(carte)
    icone_parking_html = charger_template("icone_parking.html")

    for parking in donnees_parkings["elements"]:
        folium.Marker(
            location=[parking["lat"], parking["lon"]],
            icon=folium.DivIcon(html=icone_parking_html, icon_size=(20, 20)),
            popup=noms_parkings_uniques[str(parking["id"])]
        ).add_to(groupe_parkings)

    groupe_arrets = MarkerCluster(name="Arrêts transport public", show=False).add_to(carte)
    icone_bus_html = charger_template("icone_bus.html")
    icone_train_html = charger_template("icone_train.html")

    for index, row in gdf_arrets.iterrows():
        icone = icone_train_html if row["type"] == "train" else icone_bus_html
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            icon=folium.DivIcon(html=icone, icon_size=(28, 28)),
            popup=row["nom"]
        ).add_to(groupe_arrets)

    style_commun_html = "<style>\n" + charger_template("style.css") + "</style>"
    carte.get_root().html.add_child(folium.Element(style_commun_html))

    titre_html = charger_template("titre.html")
    carte.get_root().html.add_child(folium.Element(titre_html))

    legende_html = charger_template("legende.html")
    carte.get_root().html.add_child(folium.Element(legende_html))

    folium.LayerControl().add_to(carte)

    script_case_difficulte = "<script>\n" + charger_template("case_difficulte.js") + "</script>"
    carte.get_root().html.add_child(folium.Element(script_case_difficulte))

    infos_segments_json = json.dumps(infos_segments_js)
    infos_itineraires_json = json.dumps(infos_itineraires_js)

    # Points de départ possibles (parkings et arrêts de transport public), pour le planificateur d'itinéraire.
    infos_points_recherche = []
    for parking in donnees_parkings["elements"]:
        infos_points_recherche.append({"nom": noms_parkings_uniques[str(parking["id"])], "lat": parking["lat"], "lon": parking["lon"]})
    for index, row in gdf_arrets.iterrows():
        infos_points_recherche.append({"nom": row["nom"], "lat": row.geometry.y, "lon": row.geometry.x})
    infos_points_recherche_json = json.dumps(infos_points_recherche)

    # Coordonnées de chaque jonction du réseau (pour trouver la plus proche d'un point de départ choisi) + l'arrêt le plus proche de chaque jonction (pour repérer les destinations atteignables).
    noeud_coords_2056 = {id_noeud: coordonnee for coordonnee, id_noeud in id_par_coordonnee.items()}
    points_noeuds_2056 = [Point(noeud_coords_2056[i]) for i in range(len(noeud_coords_2056))]
    points_noeuds_4326 = gpd.GeoSeries(points_noeuds_2056, crs="EPSG:2056").to_crs("EPSG:4326")
    noeud_coords_js = {i: [points_noeuds_4326.iloc[i].y, points_noeuds_4326.iloc[i].x] for i in range(len(points_noeuds_4326))}
    noeud_coords_json = json.dumps(noeud_coords_js)
    infos_noeuds_json = json.dumps(infos_noeuds)

    panneau_selection_html = charger_template("panneau_itineraire.html")
    carte.get_root().html.add_child(folium.Element(panneau_selection_html))

    panneau_recherche_html = charger_template("recherche_depart.html")
    carte.get_root().html.add_child(folium.Element(panneau_recherche_html))

    script_selection_multiple = charger_template("selection_itineraire.js")
    script_selection_multiple = script_selection_multiple.replace("__INFOS_SEGMENTS__", infos_segments_json)
    script_selection_multiple = script_selection_multiple.replace("__INFOS_ITINERAIRES__", infos_itineraires_json)
    script_selection_multiple = script_selection_multiple.replace("__INFOS_POINTS__", infos_points_recherche_json)
    script_selection_multiple = script_selection_multiple.replace("__NOEUD_COORDS__", noeud_coords_json)
    script_selection_multiple = script_selection_multiple.replace("__INFOS_NOEUDS__", infos_noeuds_json)
    script_selection_multiple = script_selection_multiple.replace("__GROUPE_DIFFICULTE__", groupe_difficulte.get_name())
    script_selection_multiple = script_selection_multiple.replace("__GROUPE_ITINERAIRES__", groupe_itineraires.get_name())
    script_selection_multiple = script_selection_multiple.replace("__CARTE__", carte.get_name())
    script_selection_multiple = "<script>\n" + script_selection_multiple + "</script>"
    carte.get_root().html.add_child(folium.Element(script_selection_multiple))

    carte.save("randonnee.html")


def main():
    donnees_parkings = charger_json_avec_cache("parkings_bruts.json", lambda: interroger_overpass(REQUETE_PARKINGS))
    noms_parkings_uniques = geocoder_noms_parkings(donnees_parkings)

    donnees_arrets = charger_json_avec_cache("arrets_bruts.json", lambda: interroger_overpass(REQUETE_ARRETS))
    donnees_itineraires = charger_json_avec_cache("itineraires_bruts.json", lambda: interroger_overpass(REQUETE_ITINERAIRES))

    gdf_itineraires, gdf_itineraires_2056 = construire_itineraires_osm(donnees_itineraires)
    gdf_arrets, gdf_arrets_2056 = construire_arrets_transport(donnees_arrets)

    gdf_itineraires["accessible_transport"] = calculer_accessibilite_transport(gdf_itineraires_2056, gdf_arrets_2056)
    print(f"{sum(gdf_itineraires['accessible_transport'])}/{len(gdf_itineraires)} itinéraires accessibles en transport public (moins de {DISTANCE_MAX_ARRET}m)")

    lignes, difficultes, noms, denivele_positif, denivele_negatif = charger_sentiers_officiels()
    segments_connectes = filtrer_et_connecter_segments(lignes, difficultes, noms, denivele_positif, denivele_negatif)
    id_par_coordonnee = segments_connectes["id_par_coordonnee"]

    infos_noeuds = associer_arrets_aux_noeuds(id_par_coordonnee, gdf_arrets_2056)

    donnees_osm_difficiles, elements_difficiles, elements_difficiles_hors_reseau = reclasser_segments_difficiles(segments_connectes)

    gdf_sentiers_2056 = gpd.GeoDataFrame({
        "difficulte": segments_connectes["difficultes"],
        "nom": segments_connectes["noms"],
        "geometry": segments_connectes["lignes"],
        "denivele_positif": segments_connectes["denivele_positif"],
        "denivele_negatif": segments_connectes["denivele_negatif"],
        "noeud_debut": segments_connectes["noeuds_debut"],
        "noeud_fin": segments_connectes["noeuds_fin"]
    }, crs="EPSG:2056")
    gdf_sentiers_2056["longueur_km"] = gdf_sentiers_2056.geometry.length / 1000
    gdf_sentiers = gdf_sentiers_2056.to_crs("EPSG:4326")

    construire_carte(
        gdf_sentiers, infos_noeuds, donnees_osm_difficiles, elements_difficiles, elements_difficiles_hors_reseau,
        gdf_itineraires, donnees_parkings, noms_parkings_uniques, gdf_arrets, id_par_coordonnee
    )


if __name__ == "__main__":
    main()
