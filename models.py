from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


# ==================================================================
# UTILISATEURS & RÔLES
# ==================================================================
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # admin / moderateur / user
    filiere_id = db.Column(db.Integer, db.ForeignKey("filieres.id"), nullable=True)
    is_active_account = db.Column(db.Boolean, default=True)
    expire_le = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    filiere = db.relationship("Filiere", backref="users")
    created_by = db.relationship("User", remote_side=[id], backref="created_users")
    epreuves = db.relationship("Epreuve", backref="auteur", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ---------- Helpers de rôle ----------
    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_moderateur(self):
        return self.role == "moderateur"

    @property
    def is_simple_user(self):
        return self.role == "user"

    @property
    def is_active(self):
        return self.is_active_account

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


# ==================================================================
# FILIÈRES / MATIÈRES
# ==================================================================
class Filiere(db.Model):
    __tablename__ = "filieres"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    nom = db.Column(db.String(100), nullable=False)

    matieres = db.relationship("Matiere", backref="filiere", cascade="all, delete-orphan")


class Matiere(db.Model):
    __tablename__ = "matieres"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(150), nullable=False)
    niveau = db.Column(db.String(10), nullable=False)
    filiere_id = db.Column(db.Integer, db.ForeignKey("filieres.id"), nullable=False)

    epreuves = db.relationship("Epreuve", backref="matiere", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("filiere_id", "slug", "niveau"),)


# ==================================================================
# ÉPREUVES
# ==================================================================
class Epreuve(db.Model):
    __tablename__ = "epreuves"
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    session = db.Column(db.String(50), nullable=False)
    annee = db.Column(db.Integer, nullable=False)
    section = db.Column(db.String(20), nullable=False)
    groupes = db.Column(db.String(50), default="Tous")
    taille = db.Column(db.String(20))
    fichier = db.Column(db.String(255), nullable=False)
    fichier_corrige = db.Column(db.String(255), nullable=True)
    
    @property
    def a_corrige(self):
        return bool(self.fichier_corrige)
    # ⬆️ FIN DE L'AJOUT ⬆️
    matiere_id = db.Column(db.Integer, db.ForeignKey("matieres.id"), nullable=False)
    ajoute_par_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "session": self.session,
            "annee": self.annee,
            "section": self.section,
            "groupes": self.groupes,
            "taille": self.taille,
            "fichier": self.fichier,
            "matiere": self.matiere.label,
            "matiere_slug": self.matiere.slug,
            "filiere": self.matiere.filiere.code,
            "niveau": self.matiere.niveau,
        }

class Exercice(db.Model):
    __tablename__ = "exercices"
    id = db.Column(db.Integer, primary_key=True)
    titre = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    annee = db.Column(db.Integer)
    fichier = db.Column(db.String(255), nullable=False)          # PDF de l'énoncé
    fichier_corrige = db.Column(db.String(255))                  # PDF du corrigé (optionnel)
    matiere_id = db.Column(db.Integer, db.ForeignKey("matieres.id"), nullable=False)
    ajoute_par_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    matiere = db.relationship("Matiere", backref="exercices")
    auteur = db.relationship("User", backref="exercices")

    @property
    def a_corrige(self):
        return bool(self.fichier_corrige)

    def to_dict(self):
        return {
            "id": self.id,
            "titre": self.titre,
            "description": self.description,
            "annee": self.annee,
            "fichier": self.fichier,
            "fichier_corrige": self.fichier_corrige,
            "a_corrige": self.a_corrige,
            "matiere": self.matiere.label,
            "matiere_slug": self.matiere.slug,
            "filiere": self.matiere.filiere.code,
            "niveau": self.matiere.niveau,
        }

# ==================================================================
# JOURNAL D'ACTIONS
# ==================================================================
class LogAction(db.Model):
    __tablename__ = "logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="logs")


class Paiement(db.Model):
    __tablename__ = "paiements"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    epreuve_id = db.Column(db.Integer, db.ForeignKey("epreuves.id"), nullable=True)
    exercice_id = db.Column(db.Integer, db.ForeignKey("exercices.id"), nullable=True)
    transaction_id = db.Column(db.String(100), unique=True, nullable=False)
    montant = db.Column(db.Integer, default=100)
    devise = db.Column(db.String(10), default="XOF")
    telephone = db.Column(db.String(20))
    operateur = db.Column(db.String(20))
    statut = db.Column(db.String(20), default="pending")
    autorise = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime)

    exercice = db.relationship("Exercice", backref="paiements")
    epreuve = db.relationship("Epreuve", backref="paiements")
    user = db.relationship("User", backref="paiements")


class Abonnement(db.Model):
    __tablename__ = "abonnements"
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(100), unique=True, nullable=False)
    telephone = db.Column(db.String(20), nullable=False)
    statut = db.Column(db.String(20), default="pending")
    username_genere = db.Column(db.String(80))
    password_genere = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
