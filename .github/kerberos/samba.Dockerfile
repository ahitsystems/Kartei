# Samba AD domain controller for the Kerberos sign-in test (ci.yml, job kerberos; backend/kartei/test_kerberos_live.py)
FROM debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0
RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-ad-dc samba-ad-provision winbind ldb-tools \
 && rm -rf /var/lib/apt/lists/*
COPY samba-dc.sh /
ENTRYPOINT ["sh", "/samba-dc.sh"]
