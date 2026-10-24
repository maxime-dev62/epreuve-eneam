// -----------------------------
// HAMBURGER ET MENU MOBILE
// -----------------------------
const hamburger = document.getElementById('hamburger');
const sideMenu = document.getElementById('side-menu');

// Ouvrir / fermer menu hamburger
hamburger.addEventListener('click', () => {
    hamburger.classList.toggle('active'); // croix
    sideMenu.classList.toggle('show');    // menu sort depuis la gauche
});


// Handler global : fermer le menu au clic en dehors
document.addEventListener('click', function(e) {
    const sideMenu = document.getElementById('side-menu');
    const hamburger = document.getElementById('hamburger');

    if (!sideMenu || !hamburger) return;  // éléments absents → on sort

    if (!sideMenu.contains(e.target) && !hamburger.contains(e.target)) {
        sideMenu.classList.remove('show');
        hamburger.classList.remove('active');
    }
});
// -----------------------------
// FILIÈRE CLICK
// -----------------------------
document.querySelectorAll('.btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const filiere = btn.dataset.filiere;
        // Cacher toutes les filières
        document.querySelectorAll('.filliere-section').forEach(sec => sec.style.display='none');
        // Afficher la filière sélectionnée
        const selected = document.getElementById(filiere);
        if(selected) {
            selected.style.display='block';
        }
    });
});
document.querySelectorAll('.matieres').forEach(link=>{
    link.addEventListener('click',() =>{
        document.querySelectorAll('.filliere, .sous-btn').forEach(sec => sec.style.display='grid');
        const getid= link.getAttribute('href').substring(1); // récupérer l'id
        const getElement = document.getElementById(getid);
        getElement.style.display='grid';        
        });
            });
//----------------------------------
// FAQ CLICK
//----------------------------------

    const faqItems = document.querySelectorAll('#faq li');
    faqItems.forEach(item =>{
        const question = item.querySelector('h3');
        const answer = item.querySelector('p');
        if (answer) {
            answer.style.display='none';
            question.style.cursor='pointer';
            question.addEventListener('click',()=> {
                answer.style.display = answer.style.display === 'none' ? 'block' : 'none';
            });
        }
    });
//----------------------------------
//  FILLIERE CLICK
//----------------------------------
    const okItems = document.querySelectorAll('.fil');
    okItems.forEach(item =>{
        const question = item.querySelector('.btn');
        const answer = item.querySelector('.sous-btn');
        answer.style.display='none';
        question.addEventListener('click',()=> {
            answer.style.display = answer.style.display === 'none'? 'block' : 'none';
            });
    });
    
    


// -----------------------------
// RECHERCHE AVEC FILTRES
// -----------------------------

document.getElementById('search-btn').addEventListener('click', () => {
    
    // 1. RÉINITIALISATION RAPIDE (sans utiliser 'grid' ou 'block' en dur)
    document.querySelectorAll('.filiere-section').forEach(sec => sec.style.display = '');
    document.querySelectorAll('.fil').forEach(item => item.querySelector('.sous-btn').style.display = 'none');
    document.querySelectorAll('.filliere, .epreuve-card').forEach(sec => sec.style.display = '');

    // 2. RÉCUPÉRATION DES FILTRES
    const filieres = document.getElementById('filiere-filter').value;
    const niveaux = document.getElementById('niveau-filter').value;
    const sections = document.getElementById('section-filter').value;
    const matieres = document.getElementById('matiere-filter').value.toLowerCase().trim();
    const mat = matieres.split(' ');
    const annees = document.getElementById('annee-filter').value;
     
    let found = false;
    
    // On récupère les éléments UNE SEULE FOIS
    const filiere_section = document.querySelectorAll('.filiere-section');
    const niveau_section = document.querySelectorAll('.niveau-section');
    const fillieres = document.querySelectorAll('.filliere');
    const epreuves = document.querySelectorAll('.epreuve-card');

    epreuves.forEach(epreuve => {
        const matchSection = !sections || epreuve.dataset.section === sections;
        const matchAnnee = !annees || epreuve.dataset.annee === annees;
        const matchMatiere = !matieres || epreuve.dataset.matiere.toLowerCase().includes(matieres);

        if (matchSection && matchAnnee && matchMatiere) {
            epreuve.style.display = 'grid';
            found = true;
        } else {
            epreuve.style.display = 'none';
        }
    });

    niveau_section.forEach(niv => {
        if (niveaux && niv.dataset.niveau !== niveaux) {
            niv.style.display = 'none';
        }
    });

    
    filiere_section.forEach(sec => {
        if (filieres && sec.dataset.filiere !== filieres) {
            sec.style.display = 'none';
        }
    });



    // 7. SCROLL ET MESSAGE
    if (found) {
        window.scrollTo({top: 750, behavior:'smooth'});
    }

    document.getElementById('search-msg').textContent = found ? '' : 'Cette épreuve n\'est pas encore dans la base de données';
    
    // 8. RESET
    document.getElementById('filiere-filter').value = "";
    document.getElementById('niveau-filter').value = "";
    document.getElementById('section-filter').value = "";
    document.getElementById('matiere-filter').value = "";
    document.getElementById('annee-filter').value = "";
});



// -----------------------------
// CACHE FILIÈRES AU REFRESH
// -----------------------------
window.addEventListener('load', () => {
    document.querySelectorAll('.filiere-section').forEach(sec => sec.style.display='none');
    window.scrollTo({top: 0, behavior:'smooth'});
});

