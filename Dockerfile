FROM public.ecr.aws/unocha/python:3.13-stable

WORKDIR /srv/postile

COPY . .

RUN apk add --virtual .build-deps \
    build-base \
    protobuf-dev \
    protobuf-c-dev \
    python3-dev && \
    pip3 install \
        cython \
        setuptools && \
    pip3 install . && \
    apk del .build-deps && \
    rm -rf /root/.cache && \
    rm -rf /var/cache/apk/* && \
    mkdir -p /etc/services.d/postile && \
    # fix the damn tracerite 1.1.2 inspector.py file
    find / -name "inspector.py" -path "*/tracerite/*" -exec sed -i 's/except AttributeError, TypeError:/except (AttributeError, TypeError):/' {} \; && \
    cp postile_run /etc/services.d/postile/run

EXPOSE 80

ENTRYPOINT [ "/init" ]
