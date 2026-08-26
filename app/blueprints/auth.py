"""Authentication: sign in, sign out and password change."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.constants import AuditAction
from app.extensions import db
from app.models import Staff
from app.services import audit
from app.utils.timeutils import now

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""

        user = Staff.query.filter(
            (Staff.email == identifier.lower()) | (Staff.staff_no == identifier.upper())
        ).first()

        if user is None or not user.check_password(password):
            audit.record(
                AuditAction.LOGIN_FAILED,
                f"Failed sign-in attempt for '{identifier}'",
                commit=True,
            )
            flash("Those credentials were not recognised.", "danger")
            return render_template("auth/login.html", identifier=identifier), 401

        if not user.is_active:
            flash(
                "This account has been deactivated. Contact your HR officer.",
                "warning",
            )
            return render_template("auth/login.html", identifier=identifier), 403

        login_user(user, remember=bool(request.form.get("remember")))
        user.last_login_at = now()
        audit.record(
            AuditAction.LOGIN,
            f"{user.full_name} signed in",
            actor=user,
            entity_type="Staff",
            entity_id=user.id,
            department_id=user.department_id,
        )
        db.session.commit()

        if user.must_change_password:
            flash("Please choose a new password before continuing.", "warning")
            return redirect(url_for("auth.change_password"))

        destination = request.args.get("next")
        if destination and destination.startswith("/"):
            return redirect(destination)
        return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html", identifier="")


@auth_bp.route("/logout")
@login_required
def logout():
    audit.record(
        AuditAction.LOGOUT,
        f"{current_user.full_name} signed out",
        actor=current_user,
        entity_type="Staff",
        entity_id=current_user.id,
        department_id=current_user.department_id,
        commit=True,
    )
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not current_user.check_password(current):
            flash("Your current password is not correct.", "danger")
        elif len(new) < 8:
            flash("The new password must be at least 8 characters.", "danger")
        elif new != confirm:
            flash("The two new passwords do not match.", "danger")
        elif new == current:
            flash("The new password must differ from the current one.", "danger")
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            audit.record(
                AuditAction.PASSWORD_CHANGED,
                f"{current_user.full_name} changed their password",
                actor=current_user,
                entity_type="Staff",
                entity_id=current_user.id,
                department_id=current_user.department_id,
            )
            db.session.commit()
            flash("Your password has been updated.", "success")
            return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html")
