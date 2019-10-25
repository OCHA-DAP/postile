# PosTile 

[![Docker image](https://images.microbadger.com/badges/image/oslandia/postile.svg)](https://hub.docker.com/r/oslandia/postile/)

Fast Mapbox Vector Tile Server mainly suited for the [openmaptiles vector tile schema](https://github.com/openmaptiles/openmaptiles)

## Features

- Support for PostGIS backend through a tm2source (as generated by [OpenMapTiles](https://github.com/openmaptiles/openmaptiles))
- Support for PostGIS single layers
- Support for reading MBTiles
- On-the-fly reprojection to web mercator EPSG:3857 (only for single layers)
- Connection pooling and asynchronous requests thanks to [asyncpg](https://github.com/MagicStack/asyncpg)

## Requirements 

- Python `>= 3.6`
- for PostGIS backend, recent `st_amvt` function. At least PostGIS >= 2.4.0.


## Installation 

    pip install cython
    pip install -e .
    postile --help

## Using a Docker container

Start Postile with:

    docker run --network host oslandia/postile postile --help

## Example of serving postgis layers individually

    postile --pguser **** --pgpassword **** --pgdatabase mydb --pghost localhost --listen-port 8080 --cors

Then layer `boundaries` can be served with: 

    http://localhost:8080/boundaries/z/x/y.pbf?fields=id,name

`fields` is optional, and when absent only geometries are encoded in the vector tile.

## Preview 

The root endpoint will display a built-in viewer with `mapbox-gl-js`.
In `DEBUG` mode the same page will also add some checkboxes to show tile boundaries and collision boxes (for labels). 


---
*For a concrete example using OpenMapTiles schema see [this tutorial](https://github.com/ldgeo/postile-openmaptiles)*

