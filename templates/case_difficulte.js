window.addEventListener("load", function() {
    var listeCouches = document.querySelector(".leaflet-control-layers-overlays");
    var ligne = document.createElement("label");
    ligne.innerHTML = '<input type="checkbox" id="case-difficulte"> Afficher la difficulté';
    ligne.style.display = "block";
    ligne.style.borderTop = "1px solid #ccc";
    ligne.style.marginTop = "4px";
    ligne.style.paddingTop = "4px";
    listeCouches.appendChild(ligne);

    var niveauxFiltre = [
        {id: "filtre-hiking", label: "Facile"},
        {id: "filtre-mountain_hiking", label: "Moyen"},
        {id: "filtre-demanding_mountain_hiking", label: "Difficile"},
        {id: "filtre-alpine_hiking", label: "Très difficile"},
        {id: "filtre-non_classifie", label: "Non classifié"}
    ];

    niveauxFiltre.forEach(function(niveau) {
        var ligneFiltre = document.createElement("label");
        ligneFiltre.innerHTML = '<input type="checkbox" id="' + niveau.id + '" checked> ' + niveau.label;
        ligneFiltre.style.display = "block";
        ligneFiltre.style.fontSize = "0.85em";
        ligneFiltre.style.marginLeft = "14px";
        listeCouches.appendChild(ligneFiltre);
    });
});
