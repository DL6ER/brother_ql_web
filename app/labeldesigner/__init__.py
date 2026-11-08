from flask import Blueprint

# Before any module of the label designer reads the labels of brother_ql,
# the drivers fix them
from app.labeldesigner import drivers  # noqa: F401

bp = Blueprint('labeldesigner', __name__, template_folder = 'templates')

from app.labeldesigner import routes