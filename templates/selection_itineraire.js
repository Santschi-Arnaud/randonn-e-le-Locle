window.addEventListener("load", function() {
    var infosSegments = __INFOS_SEGMENTS__;
    var infosItineraires = __INFOS_ITINERAIRES__;
    var segmentsSelectionnes = new Set();
    var itinerairesSelectionnes = new Set();

    function mettreAJourPanneau() {
        var distance = 0, positif = 0, negatif = 0;
        segmentsSelectionnes.forEach(function(i) {
            distance += infosSegments[i].longueur_km;
            positif += infosSegments[i].denivele_positif;
            negatif += infosSegments[i].denivele_negatif;
        });
        itinerairesSelectionnes.forEach(function(i) {
            distance += infosItineraires[i].longueur_km;
            positif += infosItineraires[i].denivele_positif;
            negatif += infosItineraires[i].denivele_negatif;
        });
        document.getElementById("total-distance").innerText = distance.toFixed(1);
        document.getElementById("total-positif").innerText = positif.toFixed(0);
        document.getElementById("total-negatif").innerText = negatif.toFixed(0);

        // Règle de Naismith adaptée : 4 km/h à plat, +1h par 400m de montée, +1h par 800m de descente.
        var heures = (distance / 4) + (positif / 400) + (negatif / 800);
        var heuresEntieres = Math.floor(heures);
        var minutes = Math.round((heures - heuresEntieres) * 60);
        if (minutes === 60) {
            minutes = 0;
            heuresEntieres += 1;
        }
        document.getElementById("total-temps").innerText = heuresEntieres + "h" + (minutes < 10 ? "0" : "") + minutes;

        // Le départ conseillé, c'est l'arrêt de transport public le plus proche de n'importe quel point de la sélection.
        var meilleurArret = null;
        segmentsSelectionnes.forEach(function(i) {
            [infosSegments[i].arret_debut, infosSegments[i].arret_fin].forEach(function(arret) {
                if (arret && (meilleurArret === null || arret.distance_m < meilleurArret.distance_m)) {
                    meilleurArret = arret;
                }
            });
        });
        var elementDepart = document.getElementById("depart-conseille");
        if (meilleurArret) {
            elementDepart.innerText = meilleurArret.nom_arret + " (" + meilleurArret.distance_m.toFixed(0) + " m)";
        } else {
            elementDepart.innerText = "aucun arrêt à proximité";
        }

        var panneau = document.getElementById("panneau-mon-itineraire");
        if (segmentsSelectionnes.size + itinerairesSelectionnes.size > 0) {
            panneau.style.display = "block";
        } else {
            panneau.style.display = "none";
        }
    }

    var afficherDifficulte = false;

    // Graphe des jonctions : sur un noeud où seuls 2 segments se rejoignent, il n'y a pas vraiment de choix à faire (pas de croisement).
    // On peut donc étendre la sélection automatiquement tant qu'on ne tombe pas sur un vrai croisement (3 segments ou plus).
    var segmentsParNoeud = {};
    infosSegments.forEach(function(info, i) {
        [info.noeud_debut, info.noeud_fin].forEach(function(noeud) {
            if (noeud !== null && noeud !== undefined) {
                segmentsParNoeud[noeud] = segmentsParNoeud[noeud] || [];
                segmentsParNoeud[noeud].push(i);
            }
        });
    });

    function etendreChaine(indexDepart) {
        var chaine = new Set([indexDepart]);
        ["noeud_debut", "noeud_fin"].forEach(function(cote) {
            var noeudCourant = infosSegments[indexDepart][cote];
            var segmentCourant = indexDepart;
            while (noeudCourant !== null && noeudCourant !== undefined && segmentsParNoeud[noeudCourant] && segmentsParNoeud[noeudCourant].length === 2) {
                var suivant = segmentsParNoeud[noeudCourant].filter(function(idx) { return idx !== segmentCourant; })[0];
                if (suivant === undefined || chaine.has(suivant)) { break; }
                chaine.add(suivant);
                var infoSuivant = infosSegments[suivant];
                noeudCourant = (infoSuivant.noeud_debut === noeudCourant) ? infoSuivant.noeud_fin : infoSuivant.noeud_debut;
                segmentCourant = suivant;
            }
        });
        return chaine;
    }

    infosSegments.forEach(function(info, i) {
        var couche = window[info.nom_js];
        couche.on("click", function() {
            var chaine = etendreChaine(i);
            var onDeselectionne = segmentsSelectionnes.has(i);
            chaine.forEach(function(index) {
                var coucheChaine = window[infosSegments[index].nom_js];
                if (onDeselectionne) {
                    segmentsSelectionnes.delete(index);
                    coucheChaine.setStyle({color: afficherDifficulte ? infosSegments[index].couleur_origine : "purple", weight: 3});
                } else {
                    segmentsSelectionnes.add(index);
                    coucheChaine.setStyle({color: "deeppink", weight: 6});
                }
            });
            mettreAJourPanneau();
        });
    });

    infosItineraires.forEach(function(info, i) {
        var couches = info.noms_js.map(function(nom) { return window[nom]; });
        couches.forEach(function(couche) {
            couche.on("click", function() {
                if (itinerairesSelectionnes.has(i)) {
                    itinerairesSelectionnes.delete(i);
                    couches.forEach(function(c) { c.setStyle({color: "purple", weight: 3, opacity: 0.6}); });
                } else {
                    itinerairesSelectionnes.add(i);
                    couches.forEach(function(c) { c.setStyle({color: "deeppink", weight: 6, opacity: 1}); });
                }
                mettreAJourPanneau();
            });
        });
    });

    document.getElementById("case-difficulte").addEventListener("change", function(evenement) {
        afficherDifficulte = evenement.target.checked;
        infosSegments.forEach(function(info, i) {
            if (!segmentsSelectionnes.has(i)) {
                window[info.nom_js].setStyle({color: afficherDifficulte ? info.couleur_origine : "purple"});
            }
        });
    });

    var filtresActifs = {
        hiking: true,
        mountain_hiking: true,
        demanding_mountain_hiking: true,
        alpine_hiking: true,
        non_classifie: true
    };

    function appliquerFiltreDifficulte() {
        infosSegments.forEach(function(info, i) {
            var couche = window[info.nom_js];
            if (filtresActifs[info.difficulte]) {
                __GROUPE_DIFFICULTE__.addLayer(couche);
            } else {
                __GROUPE_DIFFICULTE__.removeLayer(couche);
                if (segmentsSelectionnes.has(i)) {
                    segmentsSelectionnes.delete(i);
                    mettreAJourPanneau();
                }
            }
        });
    }

    Object.keys(filtresActifs).forEach(function(code) {
        document.getElementById("filtre-" + code).addEventListener("change", function(evenement) {
            filtresActifs[code] = evenement.target.checked;
            appliquerFiltreDifficulte();
        });
    });

    function reinitialiserSelection() {
        segmentsSelectionnes.forEach(function(i) {
            window[infosSegments[i].nom_js].setStyle({color: afficherDifficulte ? infosSegments[i].couleur_origine : "purple", weight: 3});
        });
        segmentsSelectionnes.clear();
        itinerairesSelectionnes.forEach(function(i) {
            infosItineraires[i].noms_js.forEach(function(nom) {
                window[nom].setStyle({color: "purple", weight: 3, opacity: 0.6});
            });
        });
        itinerairesSelectionnes.clear();
    }

    document.getElementById("bouton-reset-itineraire").addEventListener("click", function() {
        reinitialiserSelection();
        mettreAJourPanneau();
    });

    document.getElementById("bouton-valider-itineraire").addEventListener("click", function() {
        infosSegments.forEach(function(info, i) {
            if (!segmentsSelectionnes.has(i)) { __GROUPE_DIFFICULTE__.removeLayer(window[info.nom_js]); }
        });
        infosItineraires.forEach(function(info, i) {
            if (!itinerairesSelectionnes.has(i)) {
                info.noms_js.forEach(function(nom) { __GROUPE_ITINERAIRES__.removeLayer(window[nom]); });
            }
        });
        document.getElementById("bouton-valider-itineraire").style.display = "none";
        document.getElementById("bouton-modifier-itineraire").style.display = "inline-block";
    });

    document.getElementById("bouton-modifier-itineraire").addEventListener("click", function() {
        infosSegments.forEach(function(info, i) {
            if (!segmentsSelectionnes.has(i)) { __GROUPE_DIFFICULTE__.addLayer(window[info.nom_js]); }
        });
        infosItineraires.forEach(function(info, i) {
            if (!itinerairesSelectionnes.has(i)) {
                info.noms_js.forEach(function(nom) { __GROUPE_ITINERAIRES__.addLayer(window[nom]); });
            }
        });
        document.getElementById("bouton-valider-itineraire").style.display = "inline-block";
        document.getElementById("bouton-modifier-itineraire").style.display = "none";
    });

    // Planificateur d'itinéraire : depuis un parking ou un arrêt choisi, on calcule le plus court chemin (Dijkstra)
    // vers chaque autre arrêt atteignable en suivant le réseau de sentiers.
    var infosPoints = __INFOS_POINTS__;
    var noeudCoords = __NOEUD_COORDS__;
    var infosNoeuds = __INFOS_NOEUDS__;

    var listeOptions = document.getElementById("liste-points-depart");
    infosPoints.forEach(function(point) {
        var option = document.createElement("option");
        option.value = point.nom;
        listeOptions.appendChild(option);
    });

    // Dès que le point de départ saisi correspond à un élément connu de la liste, on zoome dessus tout de suite.
    document.getElementById("champ-point-depart").addEventListener("input", function(evenement) {
        var point = infosPoints.find(function(p) { return p.nom === evenement.target.value; });
        if (point) {
            __CARTE__.setView([point.lat, point.lon], 15);
        }
    });

    var adjacenceNoeuds = {};
    infosSegments.forEach(function(info, i) {
        if (info.noeud_debut !== null && info.noeud_fin !== null) {
            adjacenceNoeuds[info.noeud_debut] = adjacenceNoeuds[info.noeud_debut] || [];
            adjacenceNoeuds[info.noeud_debut].push({voisin: info.noeud_fin, poids: info.longueur_km, segment: i});
            adjacenceNoeuds[info.noeud_fin] = adjacenceNoeuds[info.noeud_fin] || [];
            adjacenceNoeuds[info.noeud_fin].push({voisin: info.noeud_debut, poids: info.longueur_km, segment: i});
        }
    });

    function distanceApprox(lat1, lon1, lat2, lon2) {
        var dx = (lon2 - lon1) * 111320 * Math.cos(lat1 * Math.PI / 180);
        var dy = (lat2 - lat1) * 110540;
        return Math.sqrt(dx * dx + dy * dy);
    }

    function trouverNoeudProche(lat, lon) {
        var meilleurId = null;
        var meilleureDistance = Infinity;
        Object.keys(noeudCoords).forEach(function(id) {
            var coord = noeudCoords[id];
            var d = distanceApprox(lat, lon, coord[0], coord[1]);
            if (d < meilleureDistance) {
                meilleureDistance = d;
                meilleurId = Number(id);
            }
        });
        return meilleurId;
    }

    function dijkstra(idDepart) {
        var distances = {};
        var segmentPrecedent = {};
        var noeudPrecedent = {};
        var nonVisites = new Set(Object.keys(adjacenceNoeuds).map(Number));
        nonVisites.add(idDepart);
        distances[idDepart] = 0;

        while (nonVisites.size > 0) {
            var noeudCourant = null;
            var meilleureDistance = Infinity;
            nonVisites.forEach(function(id) {
                if (distances[id] !== undefined && distances[id] < meilleureDistance) {
                    meilleureDistance = distances[id];
                    noeudCourant = id;
                }
            });
            if (noeudCourant === null) { break; }
            nonVisites.delete(noeudCourant);
            (adjacenceNoeuds[noeudCourant] || []).forEach(function(arc) {
                var nouvelleDistance = distances[noeudCourant] + arc.poids;
                if (distances[arc.voisin] === undefined || nouvelleDistance < distances[arc.voisin]) {
                    distances[arc.voisin] = nouvelleDistance;
                    segmentPrecedent[arc.voisin] = arc.segment;
                    noeudPrecedent[arc.voisin] = noeudCourant;
                }
            });
        }
        return {distances: distances, segmentPrecedent: segmentPrecedent, noeudPrecedent: noeudPrecedent};
    }

    var segmentPrecedentGlobal = {};
    var noeudPrecedentGlobal = {};

    function afficherTrajet(idArrivee) {
        reinitialiserSelection();
        var couchesChemin = [];
        var courant = idArrivee;
        while (segmentPrecedentGlobal[courant] !== undefined) {
            var indexSegment = segmentPrecedentGlobal[courant];
            segmentsSelectionnes.add(indexSegment);
            var coucheSegment = window[infosSegments[indexSegment].nom_js];
            coucheSegment.setStyle({color: "deeppink", weight: 6});
            couchesChemin.push(coucheSegment);
            courant = noeudPrecedentGlobal[courant];
        }
        mettreAJourPanneau();
        if (couchesChemin.length > 0) {
            var groupeTemporaire = L.featureGroup(couchesChemin);
            __CARTE__.fitBounds(groupeTemporaire.getBounds(), {padding: [40, 40]});
        }
    }

    document.getElementById("bouton-chercher-trajets").addEventListener("click", function() {
        var nomSaisi = document.getElementById("champ-point-depart").value;
        var point = infosPoints.find(function(p) { return p.nom === nomSaisi; });
        var resultatsDiv = document.getElementById("resultats-trajets");
        resultatsDiv.innerHTML = "";

        if (!point) {
            resultatsDiv.innerText = "Point de départ inconnu — choisis un élément de la liste.";
            return;
        }

        var idDepart = trouverNoeudProche(point.lat, point.lon);
        var resultat = dijkstra(idDepart);
        segmentPrecedentGlobal = resultat.segmentPrecedent;
        noeudPrecedentGlobal = resultat.noeudPrecedent;

        var destinations = {};
        Object.keys(resultat.distances).forEach(function(id) {
            var infoNoeud = infosNoeuds[id];
            if (infoNoeud && infoNoeud.distance_m < 500 && infoNoeud.nom_arret !== point.nom) {
                var distanceTotale = resultat.distances[id] + infoNoeud.distance_m / 1000;
                if (!destinations[infoNoeud.nom_arret] || distanceTotale < destinations[infoNoeud.nom_arret].distance) {
                    destinations[infoNoeud.nom_arret] = {distance: distanceTotale, noeud: Number(id)};
                }
            }
        });

        var listeDestinations = Object.keys(destinations).map(function(nom) {
            return {nom: nom, distance: destinations[nom].distance, noeud: destinations[nom].noeud};
        }).sort(function(a, b) { return a.distance - b.distance; });

        if (listeDestinations.length === 0) {
            resultatsDiv.innerText = "Aucun arrêt atteignable trouvé depuis ce point.";
            return;
        }

        listeDestinations.slice(0, 8).forEach(function(dest) {
            var ligne = document.createElement("div");
            var lien = document.createElement("a");
            lien.href = "#";
            lien.innerText = dest.nom + " — " + dest.distance.toFixed(1) + " km";
            lien.addEventListener("click", function(evenement) {
                evenement.preventDefault();
                afficherTrajet(dest.noeud);
            });
            ligne.appendChild(lien);
            resultatsDiv.appendChild(ligne);
        });
    });
});
