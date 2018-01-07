#! /usr/bin/python3

import os
import sys
sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'lib'))

import flask
from jinja2 import TemplateNotFound
from werkzeug.exceptions import HTTPException

import agapi
import ipgeo

app = flask.Flask(__name__, instance_relative_config=True)
app.config.from_object('config_defaults')
app.config.from_pyfile('config.py', silent=True)

def expand_relative_config_paths():
    base_dir = app.config.get('BASE_DIR')
    if not base_dir: base_dir = '.'
    base_dir = os.path.abspath(os.path.join(app.instance_path, base_dir))

    for k in 'REPORT_DIR', 'GPG_HOME', 'ALL_LANDMARKS', 'GEOIP_DB':
        app.config[k] = os.path.abspath(os.path.join(base_dir, app.config[k]))

expand_relative_config_paths()

geodb = ipgeo.GeoLite2City(app.config['GEOIP_DB'])

@app.context_processor
def augment_template_context():
    return {
        # Reverse routing shorthands
        's': lambda f: flask.url_for('static', filename=f),
        'p': lambda p: flask.url_for('page', page=p)
    }

# Pages
@app.route('/', defaults={'page': 'index'})
@app.route('/<page>')
def page(page):
    # Subroutine templates start with an underscore.
    if page[0] == '_':
        flask.abort(404)

    # Strip an .html extension if any.  But don't waste time issuing a
    # redirection if it's just going to land on a 404.
    if page.endswith('.html'):
        try:
            app.jinja_env.get_template(page)
        except TemplateNotFound:
            flask.abort(404)

        target = page[:-5]
        return flask.redirect(flask.url_for('page', page=target))

    try:
        lon, lat = geodb.get(flask.request.remote_addr.partition(':')[0])
        return flask.render_template(page + '.html',
                                     geoip_lon=lon,
                                     geoip_lat=lat)
    except TemplateNotFound:
        flask.abort(404)

# API
@app.route('/api/1/landmark-list')
def landmark_list():
    return agapi.landmark_list(flask.request, app.config, app.logger,
                               locations=False)

@app.route('/api/1/landmark-list-with-locations')
def landmark_list_locs():
    return agapi.landmark_list(flask.request, app.config, app.logger,
                               locations=True)

@app.route('/api/1/probe-results', methods=['POST'])
def probe_results():
    return agapi.probe_results(flask.request, app.config, app.logger)

# Error handling
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(500)
def handle_http_error(err):
    return flask.render_template("_error.html",
                                 errcode=err.code,
                                 errname=err.name,
                                 errdescription=err.description), err.code
