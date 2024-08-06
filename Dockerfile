FROM public.ecr.aws/unocha/python:3.9-stable

WORKDIR /srv/postile

COPY . .

RUN apk add --virtual .build-deps \
    build-base \
    protobuf-dev \
    protobuf-c-dev \
    python3-dev && \
    pip3 install cython && \
    pip3 install . && \
    apk del .build-deps && \
    rm -rf /root/.cache && \
    rm -rf /var/cache/apk/* && \
    mkdir -p /etc/services.d/postile && \
    cp postile_run /etc/services.d/postile/run

EXPOSE 80

ENTRYPOINT [ "/init" ]
