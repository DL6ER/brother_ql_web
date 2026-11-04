from flask import render_template, request
from . import bp


@bp.app_errorhandler(404)
def not_found_error(error):
    return render_template('error.html', title='Not found', message='This page does not exist.'), 404


@bp.app_errorhandler(500)
def internal_error(error):
    return render_template('error.html', title='An unexpected error has occurred',
                           message='The server could not handle the request, its log tells why.'), 500
