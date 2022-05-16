"""
Fast VectorTile server for PostGIS backend

inspired by https://github.com/openmaptiles/postserve
"""
import io
import os
import socket
import sys
import re
import argparse
import sqlite3
from pathlib import Path 

from sanic import Sanic
from sanic.log import logger
from sanic import response
from sanic_cors import CORS

import mercantile
import yaml

import asyncio
import asyncpg

from jinja2 import Environment, PackageLoader, select_autoescape

from postile.sql import single_layer

# https://github.com/openstreetmap/mapnik-stylesheets/blob/master/zoom-to-scale.txt
# map width in meters for web mercator 3857
MAP_WIDTH_IN_METRES = 40075016.68557849
TILE_WIDTH_IN_PIXELS = 256.0
STANDARDIZED_PIXEL_SIZE = 0.00028

# prepare regexp to extract the query from a tm2 table subquery
LAYERQUERY = re.compile(r'\s*\((?P<query>.*)\)\s+as\s+\w+\s*', re.IGNORECASE | re.DOTALL)

# the de facto standard projection for web mapping applications
# official EPSG code
OUTPUT_SRID = 3857

app = Sanic()

# lower zooms can take a while to generate (ie zoom 0->4) 
app.config.RESPONSE_TIMEOUT = 60 * 2

# where am i ? 
here = Path(os.path.abspath(os.path.dirname(__file__)))

jinja_env = Environment(
    loader=PackageLoader('postile', 'templates'),
    autoescape=select_autoescape(['html', 'xml'])
)

class Config:
    # postgresql DSN
    dsn = None
    # tm2source prepared query
    tm2query = None
    # sqlite3 connection
    db_sqlite = None
    # style configuration file
    style = None
    # database connection pool
    db_pg = None
    fonts = None


@app.listener('before_server_start')
async def setup_db_pg(app, loop):
    """
    initiate postgresql connection
    """
    if Config.dsn:
        try:
            Config.db_pg = await asyncpg.create_pool(Config.dsn, loop=loop)
        except socket.gaierror:
            print(f'Cannot establish connection to {Config.dsn}. \
Did you pass correct values to --pghost?')
            raise
        except asyncpg.exceptions.InvalidPasswordError:
            print(f'Cannot connect to {Config.dsn}. \
Please check values passed to --pguser and --pgpassword')
            raise


@app.listener('after_server_stop')
async def cleanup_db_pg(app, loop):
    if Config.dsn:
        await Config.db_pg.close()


def zoom_to_scale_denom(zoom):
    map_width_in_pixels = TILE_WIDTH_IN_PIXELS * (2 ** zoom)
    return MAP_WIDTH_IN_METRES / (map_width_in_pixels * STANDARDIZED_PIXEL_SIZE)


def resolution(zoom):
    """
    Takes a web mercator zoom level and returns the pixel resolution for that
    scale according to the global TILE_WIDTH_IN_PIXELS size
    """
    return MAP_WIDTH_IN_METRES / (TILE_WIDTH_IN_PIXELS * (2 ** zoom))


def prepared_query(filename):
    with io.open(filename, 'r') as stream:
        layers = yaml.load(stream, Loader=yaml.FullLoader)

    queries = []
    for layer in layers['Layer']:
        # Remove whitespaces, subquery parenthesis and final alias
        query = LAYERQUERY.match(layer['Datasource']['table']).group('query')

        query = query.replace(
            layer['Datasource']['geometry_field'],
            "st_asmvtgeom({}, {{bbox}}) as mvtgeom"
            .format(layer['Datasource']['geometry_field'])
        )
        query = query.replace('!bbox!', '{bbox}')
        query = query.replace('!scale_denominator!', "{scale_denominator}")
        query = query.replace('!pixel_width!', '{pixel_width}')
        query = query.replace('!pixel_height!', '{pixel_height}')

        query = """
            select st_asmvt(tile, '{}', 4096, 'mvtgeom')
            from ({} where st_asmvtgeom({}, {{bbox}}) is not null) as tile
        """.format(layer['id'], query, layer['Datasource']['geometry_field'])

        queries.append(query)

    return " union all ".join(queries)


@app.route('/style.json')
async def get_jsonstyle(request):
    if not Config.style:
        return response.text('no style available', status=404)

    return await response.file(
        Config.style,
        headers={"Content-Type": "application/json"}
    )

@app.route('/fonts/<fontstack:string>/<frange:string>.pbf')
async def get_fonts(request, fontstack, frange):
    if not Config.fonts:
        return response.text('no fonts available', status=404)
    return await response.file(
        Path(Config.fonts) / fontstack / f'{frange}.pbf',
        headers={"Content-Type": "application/x-protobuf"}
    )

async def get_mbtiles(request, z, x, y):
    # Flip Y coordinate because MBTiles store tiles in TMS.
    coords = (x, (1 << z) - 1 - y, z)
    cursor = Config.db_sqlite.execute("""
        SELECT tile_data 
        FROM tiles 
        WHERE tile_column=? and tile_row=? and zoom_level=?
        LIMIT 1 """, coords)

    tile = cursor.fetchone()
    if tile: 
        return response.raw(
            tile[0], 
            headers={"Content-Type": "application/x-protobuf",
                     "Content-Encoding": "gzip"})
    else: 
        return response.raw(b'', 
            headers={"Content-Type": "application/x-protobuf"})

async def get_tile_tm2(request, x, y, z):
    """
    """
    scale_denominator = zoom_to_scale_denom(z)

    # compute mercator bounds
    bounds = mercantile.xy_bounds(x, y, z)
    bbox = f"st_makebox2d(st_point({bounds.left}, {bounds.bottom}), st_point({bounds.right},{bounds.top}))"

    sql = Config.tm2query.format(
        bbox=bbox,
        scale_denominator=scale_denominator,
        pixel_width=256,
        pixel_height=256,
    )
    logger.debug(sql)

    async with Config.db_pg.acquire() as conn:
        # join tiles into one bytes string except null tiles
        rows = await conn.fetch(sql)
        pbf = b''.join([row[0] for row in rows if row[0]])

    return response.raw(
        pbf,
        headers={"Content-Type": "application/x-protobuf"}
    )

async def get_tile_postgis(request, x, y, z, layer):
    """
    Direct access to a postgis layer
    """
    if ' ' in layer:
        return response.text('bad layer name: {}'.format(layer), status=404)

    # get fields given in parameters
    fields = ',' + request.raw_args['fields'] if 'fields' in request.raw_args else ''
    # get geometry column name from query args else geom is used
    geom = request.raw_args.get('geom', 'geom')
    # compute mercator bounds
    bounds = mercantile.xy_bounds(x, y, z)

    # make bbox for filtering
    bbox = f"st_setsrid(st_makebox2d(st_point({bounds.left}, {bounds.bottom}), st_point({bounds.right},{bounds.top})), {OUTPUT_SRID})"

    # compute pixel resolution
    scale = resolution(z)

    sql = single_layer.format(**locals(), OUTPUT_SRID=OUTPUT_SRID)

    logger.debug(sql)

    async with Config.db_pg.acquire() as conn:
        rows = await conn.fetch(sql)
        pbf = b''.join([row[0] for row in rows if row[0]])

    return response.raw(
        pbf,
        headers={"Content-Type": "application/x-protobuf"}
    )

def preview(request):
    """build and return a preview page
    """
    if app.debug:
        template = jinja_env.get_template('index-debug.html')
    else:
        template = jinja_env.get_template('index.html')

    html_content = template.render(host=request.host, scheme=request.scheme)
    return response.html(html_content)

def config_tm2(tm2file):
    """Adds specific routes for tm2 source and prepare the global SQL Query

    """
    # build the SQL query for all layers found in TM2 file
    Config.tm2query = prepared_query(tm2file)
    # add route dedicated to tm2 queries
    app.add_route(get_tile_tm2, r'/<z:int>/<x:int>/<y:int>.pbf', methods=['GET'])
    app.add_route(preview, r'/', methods=['GET'])


def config_mbtiles(mbtiles):
    """Adds specific routes for mbtiles source

    """
    Config.db_sqlite = sqlite3.connect(mbtiles)
    app.add_route(get_mbtiles, r'/<z:int>/<x:int>/<y:int>.pbf', methods=['GET'])
    app.add_route(preview, r'/', methods=['GET'])


def check_file_exists(filename):
    if not os.path.exists(filename):
        print(f'file does not exists: {filename}, quitting...')
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Fast VectorTile server with PostGIS backend')
    parser.add_argument('--tm2', type=str, help='TM2 source file (yaml)')
    parser.add_argument('--mbtiles', type=str, help='read tiles from a mbtiles file')
    parser.add_argument('--style', type=str, help='GL Style to serve at /style.json')
    parser.add_argument('--pgdatabase', type=str, help='database name', default='osm')
    parser.add_argument('--pghost', type=str, help='postgres hostname', default='')
    parser.add_argument('--pgport', type=int, help='postgres port', default=5432)
    parser.add_argument('--pguser', type=str, help='postgres user', default='')
    parser.add_argument('--pgpassword', type=str, help='postgres password', default='')
    parser.add_argument('--listen', type=str, help='listen address', default='127.0.0.1')
    parser.add_argument('--listen-port', type=int, help='listen port', default=8080)
    parser.add_argument('--cors', action='store_true', help='make cross-origin AJAX possible')
    parser.add_argument('--debug', action='store_true', help='activate sanic debug mode')
    parser.add_argument('--fonts', type=str, help='fonts location')
    parser.add_argument('--workers', type=int, help='number of workers', default=1)
    parser.add_argument('--access-log', action='store_true', help='should access log be generated, slows down the server')
    args = parser.parse_args()
    
    if len(sys.argv) == 1:
        # display help message when no args are passed.
        parser.print_help()
        sys.exit(1)

    if args.tm2:
        check_file_exists(args.tm2)
        config_tm2(args.tm2)
    elif args.mbtiles: 
        check_file_exists(args.mbtiles)
        config_mbtiles(args.mbtiles)
    else:
        # no tm2 file given, switching to direct connection to postgis layers
        app.add_route(get_tile_postgis, r'/<layer>/<z:int>/<x:int>/<y:int>.pbf', methods=['GET'])
    if args.style:
        check_file_exists(args.style)
        Config.style = args.style

    if args.fonts:
        check_file_exists(args.fonts)
        Config.fonts = args.fonts

    # interpolate values for postgres connection
    if not args.mbtiles:
        Config.dsn = (
            'postgres://{pguser}:{pgpassword}@{pghost}:{pgport}/{pgdatabase}'
            .format(**args.__dict__)
        )

    if args.cors:
        CORS(app)

    # add static route for the favicon 
    app.static('/favicon.ico', str(here / 'static/favicon.ico'))

    app.run(
        workers=args.workers,
        host=args.listen,
        port=args.listen_port,
        access_log=args.access_log,
        debug=args.debug)


if __name__ == '__main__':
    main()
