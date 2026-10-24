# Cipherops ecosystem lab: Verisign's MTL-mode ldns, NSD and Unbound on one
# pinned toolchain. Every upstream input is an exact tag or commit; the commits
# for one consistent set of components are kept in ecosystem/stacks/*.env.
#
# Targets: nsd, unbound, tools (unbound + ldns signer + Python client).

ARG FEDORA_IMAGE=registry.fedoraproject.org/fedora-minimal:42

#######################################
# OpenSSL + liboqs + Verisign MTL library
#######################################
FROM ${FEDORA_IMAGE} AS base-build

ARG OPENSSL_TAG=openssl-3.6.0
ARG LIBOQS_TAG
ARG MTL_COMMIT
# Same optimisation as the main benchmark image (upstream configure.ac forces -O0).
ARG MTL_MAKE_CFLAGS="-fPIC -Wall -Wextra -O2"

RUN dnf -y install gcc git perl make cmake libtool autoconf automake bc findutils pkgconf-pkg-config \
        libevent-devel expat-devel flex bison python3 \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf

WORKDIR /opt/src

RUN git clone --depth 1 --branch ${OPENSSL_TAG} https://github.com/openssl/openssl.git \
    && cd openssl && ./config --prefix=/usr/local --libdir=lib64 \
    && make -j"$(nproc)" && make install_sw && ldconfig

RUN test -n "${LIBOQS_TAG}" \
    && git clone --depth 1 --branch ${LIBOQS_TAG} https://github.com/open-quantum-safe/liboqs.git \
    && cd liboqs && mkdir build && cd build \
    && cmake -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_INSTALL_LIBDIR=lib64 \
             -DOPENSSL_ROOT_DIR=/usr/local .. \
    && make -j"$(nproc)" && make install && ldconfig

RUN test -n "${MTL_COMMIT}" \
    && git clone https://github.com/verisign/MTL.git mtl && cd mtl && git checkout ${MTL_COMMIT} \
    && autoreconf --install \
    && ./configure --libdir=/usr/local/lib64 \
    && make -j"$(nproc)" CFLAGS="${MTL_MAKE_CFLAGS}" \
    && make install && ldconfig

#######################################
# ldns with MTL mode (key generation, zone signing, zone verification)
#######################################
FROM base-build AS ldns-build
ARG LDNS_COMMIT
RUN test -n "${LDNS_COMMIT}" \
    && git clone https://github.com/Verisign/mtl-mode-ldns.git ldns && cd ldns && git checkout ${LDNS_COMMIT} \
    && cd src && libtoolize -ci && autoreconf -fi \
    && ./configure --prefix=/usr/local --with-examples --with-ssl=/usr/local --libdir=/usr/local/lib64 \
                   --enable-pqc-algo-fl-dsa --enable-pqc-algo-ml-dsa --enable-pqc-algo-slh-dsa-sha2 \
                   --enable-pqc-algo-slh-dsa-shake --enable-pqc-algo-mayo-1 --enable-pqc-algo-mayo-2 \
                   --enable-pqc-algo-snova --enable-pqc-algo-mtl \
    && make -j"$(nproc)" && make install DESTDIR=/out

#######################################
# NSD with MTL mode (authoritative server, mtl-mode-full EDNS option)
#######################################
FROM base-build AS nsd-build
ARG NSD_COMMIT
RUN test -n "${NSD_COMMIT}" \
    && git clone https://github.com/verisign/mtl-mode-nsd.git nsd && cd nsd && git checkout ${NSD_COMMIT} \
    && cd src && touch zparser.y \
    && (cd simdzone && autoreconf -fi) \
    && libtoolize -ci && aclocal && autoconf && autoheader \
    && ./configure --prefix=/usr/local --enable-root-server --enable-draft-mtl \
    && make -j"$(nproc)" && make nsd-mem \
    && mkdir -p /out/usr/local/bin \
    && cp nsd nsd-checkconf nsd-checkzone nsd-control nsd-mem /out/usr/local/bin/

#######################################
# Unbound with MTL mode (validating recursive resolver with a ladder cache)
#######################################
FROM base-build AS unbound-build
ARG UNBOUND_COMMIT
RUN test -n "${UNBOUND_COMMIT}" \
    && git clone https://github.com/Verisign/mtl-mode-unbound.git unbound && cd unbound && git checkout ${UNBOUND_COMMIT} \
    && cd src && libtoolize -ci && autoreconf -fi \
    && ./configure --prefix=/usr/local --sbindir=/usr/local/bin --with-ssl=/usr/local \
                   --enable-pqc-algo-fl-dsa --enable-pqc-algo-ml-dsa --enable-pqc-algo-slh-dsa-sha2 \
                   --enable-pqc-algo-slh-dsa-shake --enable-pqc-algo-mayo-1 --enable-pqc-algo-mayo-2 \
                   --enable-pqc-algo-snova --enable-pqc-algo-mtl-mode \
    && make -j"$(nproc)" && make install DESTDIR=/out

#######################################
# Runtime images
#######################################
FROM ${FEDORA_IMAGE} AS runtime
RUN dnf -y install libevent expat iproute iproute-tc tcpdump procps-ng python3 \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf \
    && echo "/usr/local/lib" > /etc/ld.so.conf.d/local.conf
COPY --from=base-build /usr/local/lib64 /usr/local/lib64
COPY --from=base-build /usr/local/bin/openssl /usr/local/bin/openssl
RUN ldconfig

FROM runtime AS nsd
ARG STACK NSD_COMMIT MTL_COMMIT LIBOQS_TAG
COPY --from=nsd-build /out/usr/local /usr/local
RUN groupadd -r nsd && useradd -r -g nsd -d /var/nsd -s /sbin/nologin nsd \
    && mkdir -p /var/nsd /zones && chown nsd:nsd /var/nsd
ENV ECO_STACK=${STACK} ECO_NSD_COMMIT=${NSD_COMMIT} ECO_MTL_COMMIT=${MTL_COMMIT} ECO_LIBOQS_TAG=${LIBOQS_TAG}
ENTRYPOINT ["/bin/bash", "-c"]

FROM runtime AS unbound
ARG STACK UNBOUND_COMMIT MTL_COMMIT LIBOQS_TAG
COPY --from=unbound-build /out/usr/local /usr/local
RUN ldconfig && groupadd -r unbound && useradd -r -g unbound -d /etc/unbound -s /sbin/nologin unbound
ENV ECO_STACK=${STACK} ECO_UNBOUND_COMMIT=${UNBOUND_COMMIT} ECO_MTL_COMMIT=${MTL_COMMIT} ECO_LIBOQS_TAG=${LIBOQS_TAG}
ENTRYPOINT ["/bin/bash", "-c"]

FROM unbound AS tools
ARG STACK LDNS_COMMIT NSD_COMMIT UNBOUND_COMMIT MTL_COMMIT LIBOQS_TAG
COPY --from=ldns-build /out/usr/local /usr/local
COPY --from=nsd-build /out/usr/local/bin/nsd-checkzone /usr/local/bin/nsd-checkzone
RUN ldconfig && dnf -y install python3-pip && dnf clean all
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt
ENV ECO_STACK=${STACK} ECO_LDNS_COMMIT=${LDNS_COMMIT} ECO_NSD_COMMIT=${NSD_COMMIT} \
    ECO_UNBOUND_COMMIT=${UNBOUND_COMMIT} ECO_MTL_COMMIT=${MTL_COMMIT} \
    PYTHONPATH=/eco/src:/work/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /eco
