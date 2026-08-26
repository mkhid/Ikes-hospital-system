"""Role-based access control decorators."""
from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user


def roles_required(*roles):
    """Restrict a view to the listed portal roles."""
    allowed = {str(r) for r in roles}

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in allowed:
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def oversight_required(view):
    """HR and Admin only: hospital-wide records and configuration."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.has_oversight:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def scheduling_rights_required(view):
    """Department managers, HR and Admin: anyone who may act on a roster."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not (current_user.has_oversight or current_user.is_manager):
            flash("Only department managers can work with rosters.", "warning")
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def department_access_required(view):
    """
    Guard a view that takes a department.

    Expects the view to receive a `department` keyword or to resolve one itself;
    used together with can_manage_department checks inside the view.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        department = kwargs.get("department")
        if department is not None and not current_user.can_manage_department(department):
            abort(403)
        return view(*args, **kwargs)

    return wrapper
