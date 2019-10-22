#!/bin/bash

KEYFILE="server.key"
PASSKEYFILE="server.pass.key"
CSRFILE="server.csr"
CRTFILE="server.crt"

P12FILE="server.p12"
PASSWD="pass:secretpassphrase"

openssl genrsa -des3 -passout $PASSWD -out $PASSKEYFILE 2048
openssl rsa -passin $PASSWD -in $PASSKEYFILE -out $KEYFILE
rm $PASSKEYFILE
openssl req -new -key $KEYFILE -out $CSRFILE -subj "/C=US/ST=Bar/L=Foo/O=Dis/CN=::1"
openssl x509 -req -days 365 -in $CSRFILE -signkey $KEYFILE -out $CRTFILE

openssl pkcs12 -passout $PASSWD -export -in $CRTFILE -inkey $KEYFILE -out $P12FILE
