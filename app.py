import requests,random,string
from flask import session
import os
import zipfile
import io
from flask import send_file
import uuid
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, jsonify, send_from_directory)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from dotenv import load_dotenv
from flask import json as json

from models import db, User, Filiere, Matiere, Epreuve, LogAction,Exercice,Paiement,Abonnement

from supabase import create_client, Client

from datetime import datetime,timedelta

from flask_migrate import Migrate
load_dotenv()

app = Flask(__name__)    
# ==================================================================
# CONFIGURATION DE LA BASE DE DONNÉES (Hybride Local / Prod)
# ==================================================================
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # --- EN LIGNE (Vercel, Render, etc. avec Supabase) ---
    # On corrige l'URL pour SQLAlchemy (postgres:// -> postgresql://)
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
    
    elif DATABASE_URL.startswith("postgresql://"):
        # Supabase donne parfois ce format, on l'adapte
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
    # Options spécifiques pour le cloud (évite les déconnexions)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
else:
    # --- EN LOCAL (Ton PC) ---
    # On pointe directement vers le fichier dans le dossier 'instance'
    DB_PATH = os.path.join(app.instance_path, "eneam.db")
    
    # Au cas où le dossier instance n'existe pas encore
    os.makedirs(app.instance_path, exist_ok=True)
    
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", ""),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    UPLOAD_FOLDER=os.path.join("/tmp", "epreuves") if os.getenv("VERCEL") else os.path.join("static", "epreuves"),
)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ===== Paiement FedaPay =====
FEDAPAY_API_KEY = os.getenv("FEDAPAY_API_KEY", "")
FEDAPAY_ENV = os.getenv("FEDAPAY_ENV", "live")
FEDAPAY_BASE_URL = (
    "https://sandbox-api.fedapay.com/v1"
    if FEDAPAY_ENV == "sandbox"
    else "https://api.fedapay.com/v1"
)
PRIX_MINIMUM = 150  # FCFA
PRIX_ABONNEMENT = 3000

ALLOWED_EXTENSIONS = {"pdf"}

db.init_app(app)

migrate=Migrate(app,db)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Veuillez vous connecter."
login_manager.login_message_category = "error"

# Initialisation de Supabase (récupère les infos depuis ton .env)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# On crée le client seulement si les clés existent
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ==================================================================
# DÉCORATEURS DE PERMISSIONS
# ==================================================================
def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def moderateur_or_admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not (current_user.is_admin or current_user.is_moderateur):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ==================================================================
# HELPERS
# ==================================================================
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_unique_filename(original):
    ext = original.rsplit(".", 1)[1].lower()
    return f"{uuid.uuid4().hex}.{ext}"


def log_action(action, details=""):
    if current_user.is_authenticated:
        db.session.add(LogAction(
            user_id=current_user.id,
            action=action,
            details=details,
        ))

def doit_payer(epreuve):
    """Retourne True si l'épreuve est payante (L1)."""
    return epreuve.matiere.niveau == "L1"


def user_a_paye(user_id, epreuve_id):
    """Vérifie si l'utilisateur a déjà payé pour cette épreuve."""
    if not user_id:
        return False
    return Paiement.query.filter_by(
        user_id=user_id,
        epreuve_id=epreuve_id,
        autorise=True,
    ).first() is not None

def generer_zip_telechargement(fichier_url, fichier_corrige_url=None, nom_base="Fichier"):
    memory_file = io.BytesIO()
    
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. Télécharger et ajouter l'énoncé
        if fichier_url:
            try:
                # On extrait le nom du fichier depuis l'URL Supabase
                nom_fichier_supa = fichier_url.split("/")[-1]
                # On le télécharge depuis le bucket
                response = supabase.storage.from_("epreuves").download(nom_fichier_supa)
                zf.writestr(f"{nom_base}_Enonce.pdf", response)
            except Exception as e:
                print(f"⚠️ Impossible de télécharger l'énoncé : {e}")
                
        # 2. Télécharger et ajouter le corrigé
        if fichier_corrige_url:
            try:
                nom_corrige_supa = fichier_corrige_url.split("/")[-1]
                response_corrige = supabase.storage.from_("epreuves").download(nom_corrige_supa)
                zf.writestr(f"{nom_base}_Corrige.pdf", response_corrige)
            except Exception as e:
                print(f"⚠️ Impossible de télécharger le corrigé : {e}")
                
    memory_file.seek(0)
    return memory_file
def telephone_a_paye(telephone, epreuve_id):
    """Vérifie si ce numéro a déjà payé pour cette épreuve."""
    if not telephone:
        return False
    return Paiement.query.filter_by(
        telephone=telephone,
        epreuve_id=epreuve_id,
        autorise=True,
    ).first() is not None

# ==================================================================
# ROUTES PUBLIQUES
# ==================================================================
@app.route("/")
def index():
    """Liste publique des épreuves"""
    epreuves = Epreuve.query.all()
    epreuves_par_id = {}
    for ep in epreuves:
        epreuves_par_id.setdefault(ep.matiere.slug, []).append(ep.to_dict())

    matieres = {}
    for m in Matiere.query.all():
        key = (m.filiere.code, m.niveau)
        matieres.setdefault(key, []).append((m.slug, m.label))

    filieres = {f.code: f.nom for f in Filiere.query.order_by(Filiere.id).all()}

    return render_template(
        "index.html",
        filieres=filieres,
        matieres=matieres,
        epreuves_par_id=epreuves_par_id,
        now=datetime.utcnow()
    )

# ==================================================================
# AUTHENTIFICATION UNIFIÉE
# ==================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password) and user.is_active_account:
            if user.expire_le and user.expire_le < datetime.utcnow():
                flash("Votre abonnement a expiré. Veuillez en reprendre un ou continuer sans  abonnement", "error")
                return redirect(url_for("abonnement"))

            login_user(user)
            log_action("login", f"Connexion de {user.username}")
            db.session.commit()

            # Redirection selon le rôle
            if user.is_admin:
                return redirect(url_for("admin_dashboard"))
            if user.is_moderateur:
                return redirect(url_for("modo_dashboard"))
            return redirect(url_for("index"))

        flash("Identifiants incorrects.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    log_action("logout", f"Déconnexion de {current_user.username}")
    db.session.commit()
    logout_user()
    flash("Vous êtes déconnecté.", "success")
    return redirect(url_for("login"))


# ==================================================================
# DASHBOARD ADMIN
# ==================================================================
@app.route("/admin")
@admin_required
def admin_dashboard():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    q = request.args.get("q", "").strip()

    query = Epreuve.query
    if q:
        query = query.join(Matiere).join(Filiere).filter(
            db.or_(
                Epreuve.nom.ilike(f"%{q}%"),
                Epreuve.session.ilike(f"%{q}%"),
                Matiere.label.ilike(f"%{q}%"),
                Filiere.code.ilike(f"%{q}%"),
            )
        )

    pagination = query.order_by(Epreuve.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    filieres = Filiere.query.order_by(Filiere.code).all()
    modos = User.query.filter_by(role="moderateur").all()

    return render_template(
        "admin/dashboard.html",
        epreuves=pagination.items,
        pagination=pagination,
        filieres=filieres,
        modos=modos,
        q=q,
        stats={
            "total_epreuves": Epreuve.query.count(),
            "total_filieres": Filiere.query.count(),
            "total_matieres": Matiere.query.count(),
            "total_modos": User.query.filter_by(role="moderateur").count(),
        },
    )

# ---------- FILIÈRES ----------
@app.route("/admin/filieres")
@admin_required
def admin_filieres():
    filieres = Filiere.query.order_by(Filiere.code).all()
    return render_template("admin/filieres.html", filieres=filieres)

# ==================================================================
# ADMIN : GESTION DES EXERCICES
# ==================================================================
@app.route("/admin/exercices")
@admin_required
def admin_exercices():
    """Liste de TOUS les exercices (admin seulement)."""
    q = request.args.get("q", "").strip()

    query = Exercice.query
    if q:
        query = query.join(Matiere).join(Filiere).filter(
            db.or_(
                Exercice.titre.ilike(f"%{q}%"),
                Matiere.label.ilike(f"%{q}%"),
                Filiere.code.ilike(f"%{q}%"),
            )
        )

    exercices = query.order_by(Exercice.created_at.desc()).all()

    return render_template(
        "admin/exercices.html",
        exercices=exercices,
        q=q,
        stats={
            "total_exercices": Exercice.query.count(),
            "avec_corrige": Exercice.query.filter(Exercice.fichier_corrige.isnot(None)).count(),
            "sans_corrige": Exercice.query.filter(Exercice.fichier_corrige.is_(None)).count(),
        },
    )


@app.route("/admin/exercice/<int:exercice_id>/supprimer", methods=["POST"])
@admin_required
def admin_exercice_delete(exercice_id):
    """Admin peut supprimer n'importe quel exercice."""
    exercice = Exercice.query.get_or_404(exercice_id)

    # Supprimer les fichiers
    for f in [exercice.fichier, exercice.fichier_corrige]:
        if f:
            chemin = os.path.join("static", f)
            if os.path.exists(chemin):
                try:
                    os.remove(chemin)
                except OSError:
                    pass

    titre = exercice.titre
    db.session.delete(exercice)
    log_action("delete_exercice_admin", titre)
    db.session.commit()

    flash(f"Exercice « {titre} » supprimé 🗑️", "success")
    return redirect(url_for("admin_exercices") if current_user.is_admin else url_for("modo_dashboard"))

@app.route("/admin/filiere/<int:filiere_id>/modifier", methods=["POST"])
@admin_required
def admin_filiere_edit(filiere_id):
    filiere = Filiere.query.get_or_404(filiere_id)
    code = request.form["code"].strip().upper()
    nom = request.form["nom"].strip()

    existe = Filiere.query.filter(Filiere.code == code, Filiere.id != filiere_id).first()
    if existe:
        flash("Ce code est déjà utilisé.", "error")
        return redirect(url_for("admin_filieres"))

    filiere.code = code
    filiere.nom = nom
    log_action("edit_filiere", f"{code} - {nom}")
    db.session.commit()
    flash(f"Filière « {code} » modifiée ✅", "success")
    return redirect(url_for("admin_filieres"))


@app.route("/admin/filiere/<int:filiere_id>/supprimer", methods=["POST"])
@admin_required
def admin_filiere_delete(filiere_id):
    filiere = Filiere.query.get_or_404(filiere_id)

    if filiere.matieres:
        flash("Impossible : cette filière contient des matières.", "error")
        return redirect(url_for("admin_filieres"))

    code = filiere.code
    db.session.delete(filiere)
    log_action("delete_filiere", code)
    db.session.commit()
    flash(f"Filière « {code} » supprimée 🗑️", "success")
    return redirect(url_for("admin_filieres"))


# ---------- MATIÈRES ----------
@app.route("/admin/matieres")
@admin_required
def admin_matieres():
    matieres = Matiere.query.join(Filiere).order_by(Filiere.code, Matiere.niveau, Matiere.label).all()
    filieres = Filiere.query.order_by(Filiere.code).all()
    return render_template("admin/matieres.html", matieres=matieres, filieres=filieres)


@app.route("/admin/matiere/<int:matiere_id>/modifier", methods=["POST"])
@admin_required
def admin_matiere_edit(matiere_id):
    matiere = Matiere.query.get_or_404(matiere_id)
    slug = request.form["slug"].strip().lower().replace(" ", "_")
    label = request.form["label"].strip()
    niveau = request.form["niveau"]
    filiere_id = int(request.form["filiere_id"])

    existe = Matiere.query.filter(
        Matiere.filiere_id == filiere_id,
        Matiere.slug == slug,
        Matiere.niveau == niveau,
        Matiere.id != matiere_id,
    ).first()
    if existe:
        flash("Cette matière existe déjà pour cette filière/niveau.", "error")
        return redirect(url_for("admin_matieres"))

    matiere.slug = slug
    matiere.label = label
    matiere.niveau = niveau
    matiere.filiere_id = filiere_id
    log_action("edit_matiere", f"{label} ({niveau})")
    db.session.commit()
    flash(f"Matière « {label} » modifiée ✅", "success")
    return redirect(url_for("admin_matieres"))


@app.route("/admin/matiere/<int:matiere_id>/supprimer", methods=["POST"])
@admin_required
def admin_matiere_delete(matiere_id):
    matiere = Matiere.query.get_or_404(matiere_id)

    if matiere.epreuves:
        flash("Impossible : cette matière contient des épreuves.", "error")
        return redirect(url_for("admin_matieres"))

    label = matiere.label
    db.session.delete(matiere)
    log_action("delete_matiere", label)
    db.session.commit()
    flash(f"Matière « {label} » supprimée 🗑️", "success")
    return redirect(url_for("admin_matieres"))


# ---------- Gestion des utilisateurs (admin only) ----------
@app.route("/admin/users")
@admin_required
def admin_users():
    users = User.query.order_by(User.role, User.username).all()
    filieres = Filiere.query.order_by(Filiere.code).all()
    return render_template("admin/users.html", users=users, filieres=filieres)


@app.route("/admin/users/nouveau", methods=["POST"])
@admin_required
def admin_user_new():
    username = request.form["username"].strip()
    email = request.form.get("email", "").strip() or None
    password = request.form["password"]
    role = request.form["role"]
    filiere_id = request.form.get("filiere_id") or None

    # Un modérateur DOIT avoir une filière
    if role == "moderateur" and not filiere_id:
        flash("Un modérateur doit être assigné à une filière.", "error")
        return redirect(url_for("admin_users"))

    if User.query.filter_by(username=username).first():
        flash("Ce nom d'utilisateur existe déjà.", "error")
        return redirect(url_for("admin_users"))

    user = User(
        username=username,
        email=email,
        role=role,
        filiere_id=int(filiere_id) if filiere_id else None,
        created_by_id=current_user.id,
    )
    user.set_password(password)
    db.session.add(user)
    log_action("create_user", f"Création de {username} ({role})")
    db.session.commit()

    flash(f"Utilisateur « {username} » créé ✅", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/paiements")
@admin_required
def admin_paiements():
    # Paiements d'épreuves/exercices
    paiements = Paiement.query.order_by(Paiement.created_at.desc()).all()
    # Abonnements
    abonnements = Abonnement.query.order_by(Abonnement.created_at.desc()).all()
    
    # On sépare en cours et terminés (basé sur le statut)
    paiements_en_cours = [p for p in paiements if p.statut == "pending"]
    paiements_termines = [p for p in paiements if p.statut in ("approved", "declined", "canceled")]
    
    abos_en_cours = [a for a in abonnements if a.statut == "pending"]
    abos_termines = [a for a in abonnements if a.statut in ("approved", "declined")]

    return render_template(
        "admin/paiements.html",
        paiements_en_cours=paiements_en_cours,
        paiements_termines=paiements_termines,
        abos_en_cours=abos_en_cours,
        abos_termines=abos_termines
    )

@app.route("/admin/users/<int:user_id>/supprimer", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("Vous ne pouvez pas vous supprimer vous-même.", "error")
        return redirect(url_for("admin_users"))

    username = user.username
    db.session.delete(user)
    log_action("delete_user", f"Suppression de {username}")
    db.session.commit()

    flash(f"Utilisateur « {username} » supprimé 🗑️", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_user_toggle(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("Vous ne pouvez pas désactiver votre propre compte.", "error")
        return redirect(url_for("admin_users"))

    user.is_active_account = not user.is_active_account
    log_action("toggle_user", f"{'Activation' if user.is_active_account else 'Désactivation'} de {user.username}")
    db.session.commit()

    flash(f"Compte {'activé' if user.is_active_account else 'désactivé'} ✅", "success")
    return redirect(url_for("admin_users"))




# ==================================================================
# DASHBOARD MODÉRATEUR
# ==================================================================
@app.route("/modo")
@moderateur_or_admin_required
def modo_dashboard():
    """Le modérateur ne voit QUE ses épreuves."""
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))

    epreuves = Epreuve.query.filter_by(ajoute_par_id=current_user.id) \
                            .order_by(Epreuve.created_at.desc()).all()

    return render_template(
        "modo/dashboard.html",
        epreuves=epreuves,
        filiere=current_user.filiere,
        stats={
            "total_epreuves": len(epreuves),
        },
    )


@app.route("/modo/epreuve/nouvelle", methods=["GET", "POST"])
@moderateur_or_admin_required
def modo_epreuve_new():
    """Ajout d'épreuve : le modérateur est limité à SA filière."""
    if current_user.is_admin:
        filieres = Filiere.query.order_by(Filiere.code).all()
    else:
        filieres = [current_user.filiere]

    if request.method == "POST":
        try:
            matiere_id = int(request.form["matiere_id"])
            matiere = Matiere.query.get_or_404(matiere_id)

            # Vérification : le modérateur ne peut ajouter que dans sa filière
            if current_user.is_moderateur and matiere.filiere_id != current_user.filiere_id:
                abort(403)

            nom = request.form["nom"].strip()
            session = request.form["session"].strip()
            annee = int(request.form["annee"])
            section = request.form["section"]
            groupes = request.form.get("groupes", "Tous").strip() or "Tous"

            fichier = request.files.get("fichier")
            if not fichier or fichier.filename == "":
                flash("Un fichier PDF est obligatoire.", "error")
                return redirect(url_for("modo_epreuve_new"))

            if not allowed_file(fichier.filename):
                flash("Seuls les PDF sont acceptés.", "error")
                return redirect(url_for("modo_epreuve_new"))

            # ==========================================
            # ⬇️ UPLOAD VERS SUPABASE (ÉNONCÉ) ⬇️
            # ==========================================
            unique_name = generate_unique_filename(fichier.filename)
            file_content = fichier.read() # On lit le fichier en mémoire
            
            # Calcul de la taille pour l'affichage
            taille_ko = len(file_content) / 1024
            taille_str = f"{taille_ko:.0f} Ko" if taille_ko < 1024 else f"{taille_ko/1024:.1f} Mo"

            try:
                supabase.storage.from_("epreuves").upload(
                    path=unique_name,
                    file=file_content,
                    file_options={"content-type": "application/pdf"}
                )
                fichier_url = supabase.storage.from_("epreuves").get_public_url(unique_name)
            except Exception as e:
                flash(f"Erreur d'upload de l'énoncé : {e}", "error")
                return redirect(url_for("modo_epreuve_new"))

            # ==========================================
            # ⬇️ UPLOAD VERS SUPABASE (CORRIGÉ) ⬇️
            # ==========================================
            fichier_corrige_url = None
            corrige = request.files.get("fichier_corrige")
            
            if corrige and corrige.filename:
                if not allowed_file(corrige.filename):
                    flash("Le corrigé doit être un PDF.", "error")
                    return redirect(url_for("modo_epreuve_new"))
                
                unique_corrige = generate_unique_filename(corrige.filename)
                corrige_content = corrige.read()

                try:
                    supabase.storage.from_("epreuves").upload(
                        path=unique_corrige,
                        file=corrige_content,
                        file_options={"content-type": "application/pdf"}
                    )
                    fichier_corrige_url = supabase.storage.from_("epreuves").get_public_url(unique_corrige)
                except Exception as e:
                    flash(f"Erreur d'upload du corrigé : {e}", "error")
                    return redirect(url_for("modo_epreuve_new"))
            
            # ==========================================
            # ⬇️ SAUVEGARDE EN BASE DE DONNÉES ⬇️
            # ==========================================
            epreuve = Epreuve(
                nom=nom, session=session, annee=annee, section=section,
                groupes=groupes, taille=taille_str,  # On garde l'affichage de la taille
                fichier=fichier_url,                 # ⬅️ On enregistre l'URL complète
                fichier_corrige=fichier_corrige_url, # ⬅️ On enregistre l'URL complète
                matiere_id=matiere_id,
                ajoute_par_id=current_user.id,
            )
            db.session.add(epreuve)
            log_action("create_epreuve", f"{nom} - {matiere.label}")
            db.session.commit()

            flash(f"Épreuve « {nom} » ajoutée ✅", "success")
            return redirect(url_for("modo_dashboard"))

        except Exception as e:
            db.session.rollback()
            flash(f"Erreur : {e}", "error")

    return render_template("admin/epreuve_form.html", epreuve=None, filieres=filieres)


@app.route("/modo/epreuve/<int:epreuve_id>/modifier", methods=["GET", "POST"])
@moderateur_or_admin_required
def modo_epreuve_edit(epreuve_id):
    epreuve = Epreuve.query.get_or_404(epreuve_id)

    # Un modérateur ne peut modifier que SES épreuves
    if current_user.is_moderateur and epreuve.ajoute_par_id != current_user.id:
        abort(403)

    if current_user.is_admin:
        filieres = Filiere.query.order_by(Filiere.code).all()
    else:
        filieres = [current_user.filiere]

    if request.method == "POST":
        try:
            matiere_id = int(request.form["matiere_id"])
            matiere = Matiere.query.get_or_404(matiere_id)

            if current_user.is_moderateur and matiere.filiere_id != current_user.filiere_id:
                abort(403)

            epreuve.nom = request.form["nom"].strip()
            epreuve.session = request.form["session"].strip()
            epreuve.annee = int(request.form["annee"])
            epreuve.section = request.form["section"]
            epreuve.groupes = request.form.get("groupes", "Tous").strip() or "Tous"
            epreuve.matiere_id = matiere_id

            # ==========================================
            # ⬇️ MODIFICATION DU FICHIER (ÉNONCÉ) ⬇️
            # ==========================================
            fichier = request.files.get("fichier")
            if fichier and fichier.filename:
                if not allowed_file(fichier.filename):
                    flash("Seuls les PDF sont acceptés.", "error")
                    return redirect(url_for("modo_epreuve_edit", epreuve_id=epreuve.id))

                # 1. Supprimer l'ancien fichier de Supabase (s'il existe)
                if epreuve.fichier and epreuve.fichier.startswith("http"):
                    try:
                        ancien_nom = epreuve.fichier.split("/")[-1]
                        supabase.storage.from_("epreuves").remove([ancien_nom])
                    except Exception as e:
                        print(f"Note: impossible de supprimer l'ancien fichier : {e}")

                # 2. Uploader le nouveau fichier
                unique_name = generate_unique_filename(fichier.filename)
                file_content = fichier.read()
                
                # Calcul de la nouvelle taille
                taille_ko = len(file_content) / 1024
                epreuve.taille = f"{taille_ko:.0f} Ko" if taille_ko < 1024 else f"{taille_ko/1024:.1f} Mo"

                try:
                    supabase.storage.from_("epreuves").upload(
                        path=unique_name,
                        file=file_content,
                        file_options={"content-type": "application/pdf"}
                    )
                    # 3. Mettre à jour l'URL dans la base de données
                    epreuve.fichier = supabase.storage.from_("epreuves").get_public_url(unique_name)
                except Exception as e:
                    flash(f"Erreur d'upload : {e}", "error")
                    return redirect(url_for("modo_epreuve_edit", epreuve_id=epreuve.id))

            # ==========================================
            # ⬇️ MODIFICATION DU CORRIGÉ (S'IL EST FOURNI) ⬇️
            # ==========================================
            corrige = request.files.get("fichier_corrige")
            if corrige and corrige.filename:
                if not allowed_file(corrige.filename):
                    flash("Le corrigé doit être un PDF.", "error")
                    return redirect(url_for("modo_epreuve_edit", epreuve_id=epreuve.id))

                # 1. Supprimer l'ancien corrigé de Supabase
                if epreuve.fichier_corrige and epreuve.fichier_corrige.startswith("http"):
                    try:
                        ancien_corrige_nom = epreuve.fichier_corrige.split("/")[-1]
                        supabase.storage.from_("epreuves").remove([ancien_corrige_nom])
                    except Exception as e:
                        print(f"Note: impossible de supprimer l'ancien corrigé : {e}")

                # 2. Uploader le nouveau corrigé
                unique_corrige = generate_unique_filename(corrige.filename)
                corrige_content = corrige.read()

                try:
                    supabase.storage.from_("epreuves").upload(
                        path=unique_corrige,
                        file=corrige_content,
                        file_options={"content-type": "application/pdf"}
                    )
                    # 3. Mettre à jour l'URL du corrigé
                    epreuve.fichier_corrige = supabase.storage.from_("epreuves").get_public_url(unique_corrige)
                except Exception as e:
                    flash(f"Erreur d'upload du corrigé : {e}", "error")
                    return redirect(url_for("modo_epreuve_edit", epreuve_id=epreuve.id))
            log_action("edit_epreuve", f"{epreuve.nom}")
            db.session.commit()
            flash("Épreuve modifiée ✅", "success")

            # Redirection selon le rôle
            return redirect(url_for("modo_dashboard") if current_user.is_moderateur
                            else url_for("admin_dashboard"))

        except Exception as e:
            db.session.rollback()
            flash(f"Erreur : {e}", "error")

    return render_template("admin/epreuve_form.html", epreuve=epreuve, filieres=filieres)


@app.route("/modo/epreuve/<int:epreuve_id>/supprimer", methods=["POST"])
@moderateur_or_admin_required
def modo_epreuve_delete(epreuve_id):
    epreuve = Epreuve.query.get_or_404(epreuve_id)

    if current_user.is_moderateur and epreuve.ajoute_par_id != current_user.id:
        abort(403)

    chemin = os.path.join("static", epreuve.fichier)
    if os.path.exists(chemin):
        try:
            os.remove(chemin)
        except OSError:
            pass

    nom = epreuve.nom
    db.session.delete(epreuve)
    log_action("delete_epreuve", nom)
    db.session.commit()

    flash("Épreuve supprimée 🗑️", "success")
    return redirect(url_for("modo_dashboard") if current_user.is_moderateur
                    else url_for("admin_dashboard"))


# ==================================================================
# ADMIN : SUPPRESSION DIRECTE (n'importe quelle épreuve)
# ==================================================================
@app.route("/admin/epreuve/<int:epreuve_id>/supprimer", methods=["POST"])
@admin_required
def admin_epreuve_delete(epreuve_id):
    epreuve = Epreuve.query.get_or_404(epreuve_id)

    chemin = os.path.join("static", epreuve.fichier)
    if os.path.exists(chemin):
        try:
            os.remove(chemin)
        except OSError:
            pass

    nom = epreuve.nom
    db.session.delete(epreuve)
    log_action("delete_epreuve_admin", nom)
    db.session.commit()

    flash(f"Épreuve « {nom} » supprimée 🗑️", "success")
    return redirect(url_for("admin_dashboard"))


# ==================================================================
# ADMIN : FILIÈRES / MATIÈRES
# ==================================================================
@app.route("/admin/filiere/nouvelle", methods=["POST"])
@admin_required
def admin_filiere_new():
    code = request.form["code"].strip().upper()
    nom = request.form["nom"].strip()
    if code and nom and not Filiere.query.filter_by(code=code).first():
        db.session.add(Filiere(code=code, nom=nom))
        log_action("create_filiere", f"{code} - {nom}")
        db.session.commit()
        flash(f"Filière {code} créée ✅", "success")
    else:
        flash("Code déjà utilisé ou champs vides.", "error")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/logs")
@admin_required
def admin_logs():
    page = request.args.get("page", 1, type=int)
    per_page = 50
    action_filter = request.args.get("action", "").strip()
    user_filter = request.args.get("user_id", type=int)

    query = LogAction.query

    if action_filter:
        query = query.filter(LogAction.action == action_filter)

    if user_filter:
        query = query.filter(LogAction.user_id == user_filter)

    pagination = query.order_by(LogAction.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Liste des actions distinctes pour le filtre
    actions = db.session.query(LogAction.action).distinct().all()
    actions = [a[0] for a in actions]

    users = User.query.order_by(User.username).all()

    return render_template(
        "admin/logs.html",
        pagination=pagination,
        logs=pagination.items,
        actions=actions,
        users=users,
        action_filter=action_filter,
        user_filter=user_filter,
    )


@app.route("/admin/logs/export.csv")
@admin_required
def admin_logs_export():
    import csv
    from io import StringIO
    from flask import Response

    logs = LogAction.query.order_by(LogAction.timestamp.desc()).all()
    si = StringIO()
    si.write('\ufeff')  # BOM pour Excel
    writer = csv.writer(si, delimiter=";")
    writer.writerow(["ID", "Date", "Utilisateur", "Rôle", "Action", "Détails"])

    for log in logs:
        writer.writerow([
            log.id,
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            log.user.username if log.user else "—",
            log.user.role if log.user else "—",
            log.action,
            log.details or "",
        ])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=logs_eneam.csv"}
    )


@app.route("/admin/logs/purge", methods=["POST"])
@admin_required
def admin_logs_purge():
    """Supprime les logs de plus de 90 jours."""
    from datetime import datetime, timedelta
    limite = datetime.utcnow() - timedelta(days=90)

    count = LogAction.query.filter(LogAction.timestamp < limite).delete()
    db.session.commit()

    log_action("purge_logs", f"{count} entrées supprimées")
    db.session.commit()

    flash(f"{count} ancien(s) log(s) supprimé(s) 🗑️", "success")
    return redirect(url_for("admin_logs"))

@app.route("/admin/matiere/nouvelle", methods=["POST"])
@admin_required
def admin_matiere_new():
    filiere_id = int(request.form["filiere_id"])
    slug = request.form["slug"].strip().lower().replace(" ", "_")
    label = request.form["label"].strip()
    niveau = request.form["niveau"]

    existe = Matiere.query.filter_by(filiere_id=filiere_id, slug=slug, niveau=niveau).first()
    if existe:
        flash("Cette matière existe déjà.", "error")
    else:
        db.session.add(Matiere(slug=slug, label=label, niveau=niveau, filiere_id=filiere_id))
        log_action("create_matiere", f"{label} ({niveau})")
        db.session.commit()
        flash("Matière créée ✅", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/a-propos")
def apropos():
    stats = {
        "total_epreuves": Epreuve.query.count() + Exercice.query.count(),
        "total_filieres": Filiere.query.count(),
        "total_matieres": Matiere.query.count(),
    }
    return render_template("apropos.html", stats=stats)

@app.route("/contact")
def contact():
    return render_template("contact.html")


# ==================================================================
# EXERCICES PUBLICS
# ==================================================================
@app.route("/exercices")
def liste_exercices():
    """Liste publique des exercices, accessible à tous."""
    exercices = Exercice.query.all()

    exercices_par_id = {}
    for ex in exercices:
        exercices_par_id.setdefault(ex.matiere.slug, []).append(ex.to_dict())

    matieres = {}
    for m in Matiere.query.all():
        key = (m.filiere.code, m.niveau)
        matieres.setdefault(key, []).append((m.slug, m.label))

    filieres = {f.code: f.nom for f in Filiere.query.order_by(Filiere.id).all()}

    return render_template(
        "exercices.html",
        filieres=filieres,
        matieres=matieres,
        exercices_par_id=exercices_par_id,
    )

@app.route("/telecharger/exercice/<int:exercice_id>")
def telecharger_exercice(exercice_id):
    exercice = Exercice.query.get_or_404(exercice_id)
    if current_user.is_authenticated:
        # (Optionnel : vérifier s'il est admin ou modo, mais les users premium sont déjà connectés)
        pass 
    else:

        telephone = session.get("telephone_paiement")
        # Pour faire simple, on vérifie s'il a payé pour cet exercice
        deja_paye = Paiement.query.filter_by(
            telephone=telephone,
            exercice_id=exercice_id,
            autorise=True
        ).first() is not None

        if not deja_paye:
            flash("Cet exercice est payant (minimum 150 FCFA).", "info")
            return redirect(url_for("payer_exercice", exercice_id=exercice_id))

    # Génère le ZIP
    zip_data = generer_zip_telechargement(
        fichier_principal=exercice.fichier,
        fichier_corrige=exercice.fichier_corrige
    )
    
    nom_fichier = f"Exercice_{exercice.titre.replace(' ', '_')}.zip"
    return send_file(
        zip_data,
        mimetype='application/zip',
        as_attachment=True,
        download_name=nom_fichier
    )


# ==================================================================
# ADMIN / MODO : CRUD EXERCICES
# ==================================================================
@app.route("/modo/exercice/nouveau", methods=["GET", "POST"])
@moderateur_or_admin_required
def modo_exercice_new():
    if current_user.is_admin:
        filieres = Filiere.query.order_by(Filiere.code).all()
    else:
        filieres = [current_user.filiere]

    if request.method == "POST":
        try:
            matiere_id = int(request.form["matiere_id"])
            matiere = Matiere.query.get_or_404(matiere_id)

            if current_user.is_moderateur and matiere.filiere_id != current_user.filiere_id:
                abort(403)

            titre = request.form["titre"].strip()
            description = request.form.get("description", "").strip()
            annee = int(request.form["annee"]) if request.form.get("annee") else None

            # Fichier énoncé (obligatoire)
            # ==========================================
            # ⬇️ UPLOAD VERS SUPABASE (ÉNONCÉ) ⬇️
            # ==========================================
            fichier = request.files.get("fichier")
            if not fichier or fichier.filename == "":
                flash("Le PDF de l'énoncé est obligatoire.", "error")
                return redirect(url_for("modo_exercice_new"))

            if not allowed_file(fichier.filename):
                flash("Seuls les PDF sont acceptés.", "error")
                return redirect(url_for("modo_exercice_new"))

            unique_name = generate_unique_filename(fichier.filename)
            fichier_content = fichier.read() # Lecture en mémoire

            try:
                supabase.storage.from_("epreuves").upload(
                    path=unique_name,
                    file=fichier_content,
                    file_options={"content-type": "application/pdf"}
                )
                fichier_url = supabase.storage.from_("epreuves").get_public_url(unique_name)
            except Exception as e:
                flash(f"Erreur d'upload de l'énoncé : {e}", "error")
                return redirect(url_for("modo_exercice_new"))

            # ==========================================
            # ⬇️ UPLOAD VERS SUPABASE (CORRIGÉ) ⬇️
            # ==========================================
            fichier_corrige_url = None
            corrige = request.files.get("fichier_corrige")
            
            if corrige and corrige.filename:
                if not allowed_file(corrige.filename):
                    flash("Le corrigé doit aussi être un PDF.", "error")
                    return redirect(url_for("modo_exercice_new"))

                unique_corrige = generate_unique_filename(corrige.filename)
                corrige_content = corrige.read()

                try:
                    supabase.storage.from_("epreuves").upload(
                        path=unique_corrige,
                        file=corrige_content,
                        file_options={"content-type": "application/pdf"}
                    )
                    fichier_corrige_url = supabase.storage.from_("epreuves").get_public_url(unique_corrige)
                except Exception as e:
                    flash(f"Erreur d'upload du corrigé : {e}", "error")
                    return redirect(url_for("modo_exercice_new"))

            # ==========================================
            # ⬇️ CRÉATION DE L'EXERCICE EN BASE ⬇️
            # ==========================================
            exercice = Exercice(
                titre=titre,
                description=description,
                annee=annee,
                fichier=fichier_url,                 # ⬅️ URL Supabase
                fichier_corrige=fichier_corrige_url, # ⬅️ URL Supabase
                matiere_id=matiere_id,
                ajoute_par_id=current_user.id,
            )
            db.session.add(exercice)
            log_action("create_exercice", f"{titre} - {matiere.label}")
            db.session.commit()

            flash(f"Exercice « {titre} » ajouté ✅", "success")
            return redirect(url_for("modo_dashboard"))

        except Exception as e:
            db.session.rollback()
            flash(f"Erreur : {e}", "error")

    return render_template("modo/exercice_form.html", exercice=None, filieres=filieres)


@app.route("/modo/exercice/<int:exercice_id>/modifier", methods=["GET", "POST"])
@moderateur_or_admin_required
def modo_exercice_edit(exercice_id):
    exercice = Exercice.query.get_or_404(exercice_id)

    if current_user.is_moderateur and exercice.ajoute_par_id != current_user.id:
        abort(403)

    if current_user.is_admin:
        filieres = Filiere.query.order_by(Filiere.code).all()
    else:
        filieres = [current_user.filiere]

    if request.method == "POST":
        try:
            matiere_id = int(request.form["matiere_id"])
            matiere = Matiere.query.get_or_404(matiere_id)

            if current_user.is_moderateur and matiere.filiere_id != current_user.filiere_id:
                abort(403)

            exercice.titre = request.form["titre"].strip()
            exercice.description = request.form.get("description", "").strip()
            exercice.annee = int(request.form["annee"]) if request.form.get("annee") else None
            exercice.matiere_id = matiere_id

            # Nouveau fichier énoncé
            # ==========================================
            # ⬇️ MODIFICATION DU FICHIER (ÉNONCÉ) ⬇️
            # ==========================================
            fichier = request.files.get("fichier")
            if fichier and fichier.filename:
                if not allowed_file(fichier.filename):
                    flash("Seuls les PDF sont acceptés.", "error")
                    return redirect(url_for("modo_exercice_edit", exercice_id=exercice.id))

                # 1. Supprimer l'ancien fichier de Supabase (s'il existe)
                if exercice.fichier and exercice.fichier.startswith("http"):
                    try:
                        ancien_nom = exercice.fichier.split("/")[-1]
                        supabase.storage.from_("epreuves").remove([ancien_nom])
                    except Exception as e:
                        print(f"Note: impossible de supprimer l'ancien fichier : {e}")

                # 2. Uploader le nouveau
                unique_name = generate_unique_filename(fichier.filename)
                file_content = fichier.read()
                
                try:
                    supabase.storage.from_("epreuves").upload(
                        path=unique_name,
                        file=file_content,
                        file_options={"content-type": "application/pdf"}
                    )
                    exercice.fichier = supabase.storage.from_("epreuves").get_public_url(unique_name)
                except Exception as e:
                    flash(f"Erreur d'upload : {e}", "error")
                    return redirect(url_for("modo_exercice_edit", exercice_id=exercice.id))

            # ==========================================
            # ⬇️ MODIFICATION DU CORRIGÉ ⬇️
            # ==========================================
            corrige = request.files.get("fichier_corrige")
            if corrige and corrige.filename:
                if not allowed_file(corrige.filename):
                    flash("Le corrigé doit aussi être un PDF.", "error")
                    return redirect(url_for("modo_exercice_edit", exercice_id=exercice.id))

                # 1. Supprimer l'ancien corrigé de Supabase
                if exercice.fichier_corrige and exercice.fichier_corrige.startswith("http"):
                    try:
                        ancien_corrige_nom = exercice.fichier_corrige.split("/")[-1]
                        supabase.storage.from_("epreuves").remove([ancien_corrige_nom])
                    except Exception as e:
                        print(f"Note: impossible de supprimer l'ancien corrigé : {e}")

                # 2. Uploader le nouveau corrigé
                unique_corrige = generate_unique_filename(corrige.filename)
                corrige_content = corrige.read()

                try:
                    supabase.storage.from_("epreuves").upload(
                        path=unique_corrige,
                        file=corrige_content,
                        file_options={"content-type": "application/pdf"}
                    )
                    exercice.fichier_corrige = supabase.storage.from_("epreuves").get_public_url(unique_corrige)
                except Exception as e:
                    flash(f"Erreur d'upload du corrigé : {e}", "error")
                    return redirect(url_for("modo_exercice_edit", exercice_id=exercice.id))
            log_action("edit_exercice", exercice.titre)
            db.session.commit()
            flash("Exercice modifié ✅", "success")
            return redirect(url_for("admin_exercices") if current_user.is_admin else url_for("modo_dashboard"))

        except Exception as e:
            db.session.rollback()
            flash(f"Erreur : {e}", "error")

    return render_template("modo/exercice_form.html", exercice=exercice, filieres=filieres)


@app.route("/modo/exercice/<int:exercice_id>/supprimer", methods=["POST"])
@moderateur_or_admin_required
def modo_exercice_delete(exercice_id):
    exercice = Exercice.query.get_or_404(exercice_id)

    if current_user.is_moderateur and exercice.ajoute_par_id != current_user.id:
        abort(403)

    # Supprimer les fichiers
    for f in [exercice.fichier, exercice.fichier_corrige]:
        if f:
            chemin = os.path.join("static", f)
            if os.path.exists(chemin):
                try:
                    os.remove(chemin)
                except OSError:
                    pass

    titre = exercice.titre
    db.session.delete(exercice)
    log_action("delete_exercice", titre)
    db.session.commit()

    flash("Exercice supprimé 🗑️", "success")
    return redirect(url_for("modo_dashboard"))

# ==================================================================
# API
# ==================================================================
@app.route("/api/matieres/<int:filiere_id>")
@moderateur_or_admin_required
def api_matieres(filiere_id):
    # Un modérateur ne peut voir que sa filière
    if current_user.is_moderateur and current_user.filiere_id != filiere_id:
        abort(403)

    matieres = Matiere.query.filter_by(filiere_id=filiere_id) \
                            .order_by(Matiere.niveau, Matiere.label).all()
    return jsonify({
        "matieres": [
            {"id": m.id, "slug": m.slug, "label": m.label, "niveau": m.niveau}
            for m in matieres
        ]
    })


# ==================================================================
# ERREURS
# ==================================================================
@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


# ==================================================================
# INITIALISATION
# ==================================================================
def seed_db():
    if Filiere.query.first():
        return

    data = {
        "IG": ("Informatique de Gestion", {
            "L1": [("alg", "Algèbre linéaire"), ("alg2", "Algèbre linéaire approfondie"),
                   ("algo", "Algorithmique"), ("anafin", "Analyse Financière"),
                   ("anama", "Analyse mathématique"), ("anama2", "Analyse mathématique approfondie"),
                   ("ato", "A.T.O"), ("bureau", "Bureautique"), ("compta", "Comptabilité"),
                   ("eco1", "Economie d'entreprise"), ("eco2", "Economie générale"),
                   ("entre", "Entrepreneuriat"), ("mafin", "Mathématiques Financière"),
                   ("mainfo", "Mathématiques pour Informatique"), ("py", "Python"),
                   ("li", "Système linux"), ("teeo", "T.E.E.O"), ("design", "Web design")],
            "L2": [("base", "Base de réseaux informatiques"), ("droit_numerique", "Droit numérique"),
                   ("entrepreneuriat", "Entrepreneuriat"), ("intro_droit", "Introduction au droit"),
                   ("maintenance", "Maintenance informatique"), ("outils", "Outils d'analyse"),
                   ("programmation_evernementielle", "Programmation événementielle"),
                   ("programmation_web", "Programmation web"),
                   ("recherche_operationnelle", "Recherche opérationnelle"),
                   ("redaction_administrative", "Rédaction administrative"),
                   ("stat_pro", "Statistique et probabilité"),
                   ("statistique_inferentielle", "Statistique inférentielle"),
                   ("structure_donnee", "Structure de donnée")],
        }),
        "GFC": ("Gestion Financière et Comptable", {
            "L1": [("bcf", "Bureau comptable et fiscal"), ("cive", "CIVE"),
                   ("comptafin", "Comptabilité Financière"), ("dsc", "Droit et Système Comptable"),
                   ("eco11", "Economie d'entreprise"), ("eco22", "Economie générale"),
                   ("entre1", "Entrepreneuriat"), ("informatique", "Informatique"),
                   ("droit", "Initiation au Droit"), ("igrh", "Introduction à la GRH"),
                   ("imanager", "Introduction au management"), ("imarketing", "Introduction au marketing"),
                   ("macro", "Macroéconomique"), ("mafin1", "Mathématiques Financières"),
                   ("mathgen", "Mathématiques Générales"), ("micro", "Microéconomique"),
                   ("oam1", "Outils et Application Management"), ("oam2", "Outils et Application Marketing"),
                   ("sp", "Statistique et probabilité"), ("teeo1", "T.E.E.O"), ("transport", "Transport")],
        }),
        "plan": ("Planification", {
            "L1": [("alglin", "Algèbre Linéaire"), ("anamath", "Analyse Mathématique"),
                   ("cive1", "CIVE"), ("comptafin1", "Comptabilité Financière"),
                   ("comptanation1", "Comptabilité nationale"), ("devperso", "Développement Personnel"),
                   ("droit1", "Droit"), ("entre2", "Entrepreneuriat"), ("gdq", "Gestion de qualité"),
                   ("hpe", "Histoire de la Pensée Economique"), ("informatique1", "Informatique"),
                   ("introplan", "Introduction à la planification"), ("macro1", "Macroéconomique"),
                   ("micro1", "Microéconomique"), ("sociologie", "Sociologie des Organisations"),
                   ("sd", "Statistique Descriptive"), ("probabilite", "Probabilité")],
            "L2": [("est", "Econométrie des Séries Temporelles"), ("at", "Aménagement du Territoire")],
        }),
        "MM": ("Marketing & Management", {}),
        "BF": ("Banque & Finance", {}),
        "AL": ("Administration & Logistique", {}),
    }

    for code, (nom, niveaux) in data.items():
        f = Filiere(code=code, nom=nom)
        db.session.add(f)
        db.session.flush()
        for niveau, liste in niveaux.items():
            for slug, label in liste:
                db.session.add(Matiere(slug=slug, label=label, niveau=niveau, filiere_id=f.id))

    db.session.commit()
    print("✅ Base initialisée.")


# ==================================================================
# PAIEMENT MOMO (FedaPay)
# ==================================================================
@app.route("/telecharger/epreuve/<int:epreuve_id>")
def telecharger_epreuve(epreuve_id):
    epreuve = Epreuve.query.get_or_404(epreuve_id)
    if current_user.is_authenticated:
        # (Optionnel : vérifier s'il est admin ou modo, mais les users premium sont déjà connectés)
        pass 
    else:

        # Si l'épreuve est payante (L1)
        if doit_payer(epreuve):
            # On vérifie si le numéro de téléphone (stocké dans la session) a payé
            telephone = session.get("telephone_paiement")
            
            if not telephone or not telephone_a_paye(telephone, epreuve_id):
                flash("Cette épreuve est payante. Veuillez effectuer le paiement.", "info")
                return redirect(url_for("payer_epreuve", epreuve_id=epreuve_id))

    # Si c'est gratuit ou déjà payé, on génère le ZIP
    zip_data = generer_zip_telechargement(
        fichier_principal=epreuve.fichier, 
        fichier_corrige=epreuve.fichier_corrige
    )
    nom_fichier = f"{epreuve.nom.replace(' ', '_')}_{epreuve.session}.zip"
    return send_file(zip_data, mimetype='application/zip', as_attachment=True, download_name=nom_fichier)

@app.route("/payer/<int:epreuve_id>", methods=["GET", "POST"])
def payer_epreuve(epreuve_id):
    epreuve = Epreuve.query.get_or_404(epreuve_id)

    if request.method == "POST":
        montant = int(request.form.get("montant", 0))
        telephone = request.form.get("telephone", "").strip()
        operateur = request.form.get("operateur", "").strip()

        if montant < PRIX_MINIMUM:
            flash(f"Le montant minimum est de {PRIX_MINIMUM} FCFA.", "error")
            return redirect(url_for("payer_epreuve", epreuve_id=epreuve_id))

        try:
            # --- FedaPay ---
            headers = {"Authorization": f"Bearer {FEDAPAY_API_KEY}", "Content-Type": "application/json"}
            data = {
                "description": f"Épreuve L1 : {epreuve.nom}",
                "amount": montant,
                "currency": {"iso": "XOF"},
                "callback_url": url_for("paiement_retour", _external=True),
                "customer": {"firstname": "Eleve", "email": "eleve@eneam.edu"}, # Pas besoin de compte
            }
            r = requests.post(f"{FEDAPAY_BASE_URL}/transactions", json=data, headers=headers, timeout=15)
            r.raise_for_status()
            transaction_id = r.json()["v1/transaction"]["id"]

            # On enregistre avec user_id=None (car pas de compte)
            paiement = Paiement(
                user_id=None, 
                epreuve_id=epreuve_id,
                transaction_id=str(transaction_id),
                montant=montant,
                telephone=telephone,
                operateur=operateur,
                statut="pending",
            )
            db.session.add(paiement)
            db.session.commit()

            # On sauvegarde le téléphone en session pour le retour
            session["telephone_paiement"] = telephone

            # Redirection FedaPay
            r2 = requests.post(f"{FEDAPAY_BASE_URL}/transactions/{transaction_id}/token", headers=headers, timeout=15)
            r2.raise_for_status()
            return redirect(f"https://process.fedapay.com/{r2.json()['token']}")

        except Exception as e:
            db.session.rollback()
            flash(f"Erreur paiement : {e}", "error")

    return render_template("paiement.html", item=epreuve, prix_minimum=PRIX_MINIMUM)
@app.route("/payer/exercice/<int:exercice_id>", methods=["GET", "POST"])
def payer_exercice(exercice_id):
    exercice = Exercice.query.get_or_404(exercice_id)

    # Vérifie s'il a déjà payé
    #deja_paye = Paiement.query.filter_by(user_id=current_user.id, exercice_id=exercice_id, autorise=True).first()
    #if deja_paye:
        #return redirect(url_for("telecharger_exercice", exercice_id=exercice_id))

    if request.method == "POST":
        montant = int(request.form.get("montant", 0))
        telephone = request.form.get("telephone", "").strip()
        operateur = request.form.get("operateur", "").strip()

        if montant < PRIX_MINIMUM:
            flash(f"Le montant minimum est de {PRIX_MINIMUM} FCFA.", "error")
            return redirect(url_for("payer_exercice", exercice_id=exercice_id))

        try:
            # Appel API FedaPay (identique à celui des épreuves)
            headers = {"Authorization": f"Bearer {FEDAPAY_API_KEY}", "Content-Type": "application/json"}
            data = {
                "description": f"Exercice : {exercice.titre}",
                "amount": montant,
                "currency": {"iso": "XOF"},
                "callback_url": url_for("paiement_retour", _external=True),
                "customer": {"firstname": current_user.username, "email": current_user.email or "eleve@eneam.edu"},
            }
            r = requests.post(f"{FEDAPAY_BASE_URL}/transactions", json=data, headers=headers, timeout=15)
            r.raise_for_status()
            transaction_id = r.json()["v1/transaction"]["id"]

            # Enregistre le paiement avec l'exercice_id
            paiement = Paiement(
                user_id=current_user.id,
                exercice_id=exercice_id, # ⬅️ ICI
                transaction_id=str(transaction_id),
                montant=montant,
                telephone=telephone,
                operateur=operateur,
                statut="pending",
            )
            db.session.add(paiement)
            db.session.commit()

            # Redirige vers FedaPay
            r2 = requests.post(f"{FEDAPAY_BASE_URL}/transactions/{transaction_id}/token", headers=headers, timeout=15)
            r2.raise_for_status()
            token = r2.json()["token"]
            return redirect(f"https://process.fedapay.com/{token}")

        except requests.RequestException as e:
            db.session.rollback()
            flash(f"Erreur paiement : {e}", "error")

    return render_template("paiement.html", item=exercice, type_item="exercice", prix_minimum=PRIX_MINIMUM)

@app.route("/paiement/retour")
def paiement_retour():
    telephone = session.get("telephone_paiement")
    if not telephone:
        return redirect(url_for("liste_epreuves"))

    # Cherche le dernier paiement de ce téléphone
    paiement = Paiement.query.filter_by(telephone=telephone).order_by(Paiement.created_at.desc()).first()

    if paiement and paiement.autorise:
        flash("✅ Paiement confirmé !", "success")
        return redirect(url_for("telecharger_epreuve", epreuve_id=paiement.epreuve_id))

    flash("⏳ Vérification du paiement. Réessayez dans 30 secondes.", "info")
    return redirect(url_for("liste_epreuves"))

@app.route("/webhook/fedapay", methods=["POST"])
def paiement_webhook():
    """Appelé par FedaPay quand le paiement est confirmé."""
    data = request.get_json(silent=True) or {}
    event = data.get("event", "")
    entity = data.get("entity", {})
    transaction_id = str(entity.get("id", ""))

    abo = Abonnement.query.filter_by(transaction_id=transaction_id).first()
    if abo and event == "transaction.approved":
        abo.statut = "approved"
        
        # Générer un nom d'utilisateur et mot de passe
        username = f"premium_{abo.telephone[-4:]}_{random.randint(100,999)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        
        # Créer le compte User (rôle "user", valide 30 jours)
        new_user = User(
            username=username,
            role="user",
            telephone=abo.telephone, # (Optionnel, si t'as la colonne)
            expire_le=datetime.utcnow() + timedelta(days=30)
        )
        new_user.set_password(password)
        db.session.add(new_user)
        
        abo.username_genere = username
        abo.password_genere = password
        db.session.commit()

        
        return "Compte premium créé", 200


    if not transaction_id:
        return "", 400

    paiement = Paiement.query.filter_by(transaction_id=transaction_id).first()
    if not paiement:
        return "", 404

    if event == "transaction.approved":
        paiement.statut = "approved"
        paiement.autorise = True
        paiement.paid_at = datetime.utcnow()
    elif event == "transaction.declined":
        paiement.statut = "declined"
    elif event == "transaction.canceled":
        paiement.statut = "canceled"

    db.session.commit()
    return "", 200

@app.route("/abonnement")
def abonnement():
    return render_template("abonnement.html", prix=PRIX_ABONNEMENT)

@app.route("/abonnement/payer", methods=["POST"])
def payer_abonnement():
    telephone = request.form.get("telephone", "").strip()
    if not telephone:
        flash("Numéro de téléphone obligatoire.", "error")
        return redirect(url_for("abonnement"))

    try:
        headers = {"Authorization": f"Bearer {FEDAPAY_API_KEY}", "Content-Type": "application/json"}
        data = {
            "description": "Abonnement Premium 30 jours - ENEAM",
            "amount": PRIX_ABONNEMENT,
            "currency": {"iso": "XOF"},
            # L'URL de retour pour l'abonnement
            "callback_url": url_for("abonnement_retour", _external=True),
            "customer": {"firstname": "Abonne", "email": f"{telephone}@eneam.edu"},
        }
        r = requests.post(f"{FEDAPAY_BASE_URL}/transactions", json=data, headers=headers, timeout=15)
        r.raise_for_status()
        transaction_id = r.json()["v1/transaction"]["id"]

        abo = Abonnement(transaction_id=str(transaction_id), telephone=telephone, statut="pending")
        db.session.add(abo)
        db.session.commit()

        r2 = requests.post(f"{FEDAPAY_BASE_URL}/transactions/{transaction_id}/token", headers=headers, timeout=15)
        r2.raise_for_status()
        return redirect(f"https://process.fedapay.com/{r2.json()['token']}")

    except Exception as e:
        db.session.rollback()
        flash(f"Erreur : {e}", "error")
        return redirect(url_for("abonnement"))

@app.route("/abonnement/retour")
def abonnement_retour():
    # On cherche le dernier abonnement en attente de ce téléphone (ou via session)
    # Pour faire simple, on affiche un message d'attente
    flash("Vérification du paiement en cours... Si votre compte est créé, regardez votre téléphone.", "info")
    return redirect(url_for("login"))


with app.app_context():
    db.create_all()
    seed_db()


if __name__ == "__main__":
    app.run(debug=True,port=5002)
