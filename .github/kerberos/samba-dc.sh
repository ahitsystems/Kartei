#!/bin/sh
# Domain KARTEI.TEST with the test users, then the keytab for Kartei's service name in /out (bind mount).
#   alice: in kartei-read (gets a Kartei user)   bob: in kartei-read, disabled   carol: no Kartei group
set -e
PW="Test-pw-2026"
rm -f /etc/samba/smb.conf
samba-tool domain provision --realm=KARTEI.TEST --domain=KARTEI --server-role=dc --dns-backend=NONE \
  --adminpass="$PW"
# Kartei's LDAP bind in the test is a simple bind over ldap:// (no TLS set up here)
sed -i '/^\[global\]/a \	ldap server require strong auth = no' /etc/samba/smb.conf
for u in alice bob carol; do
  samba-tool user create "$u" "$PW" --given-name="$u" --surname=Test --mail-address="$u@kartei.test"
done
samba-tool group add kartei-read
samba-tool group addmembers kartei-read alice,bob
samba-tool user disable bob
samba-tool user create svc-kartei-http --random-password
samba-tool spn add HTTP/kartei.kartei.test svc-kartei-http
# AES only, as the manual asks for in AD: without it the KDC issues RC4 tickets, which newer Kerberos libraries can't read
ldbmodify -H /var/lib/samba/private/sam.ldb <<EOF
dn: CN=svc-kartei-http,CN=Users,DC=kartei,DC=test
changetype: modify
replace: msDS-SupportedEncryptionTypes
msDS-SupportedEncryptionTypes: 24
EOF
samba-tool domain exportkeytab /out/http.keytab --principal=HTTP/kartei.kartei.test
chmod 644 /out/http.keytab
exec samba -i
