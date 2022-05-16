FROM debian:bullseye-slim

RUN apt-get update \
    && apt-get install -y git libprotobuf-dev libprotobuf-c-dev python3.9 python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/postile

RUN cd /opt/postile \
    && pip3 install cython \
    && pip3 install .

CMD ["/usr/local/bin/postile"]
