#!/bin/bash

PREFIX="jsonrpc-tests"
KEYFILE="$PREFIX.server.key"
PASSKEYFILE="$PREFIX.server.pass.key"
CSRFILE="$PREFIX.server.csr"
CRTFILE="$PREFIX.server.crt"
P12FILE="$PREFIX.p12"
PASSWD="pass:x"

openssl genrsa -des3 -passout $PASSWD -out $PASSKEYFILE 2048
openssl rsa -passin $PASSWD -in $PASSKEYFILE -out $KEYFILE
rm $PASSKEYFILE
openssl req -new -key $KEYFILE -out $CSRFILE -subj "/C=US/ST=Bar/L=Foo/O=Dis/CN=127.0.0.1"
openssl x509 -req -days 365 -in $CSRFILE -signkey $KEYFILE -out $CRTFILE
openssl pkcs12 -passout $PASSWD -export -in $CRTFILE -inkey $KEYFILE -out $P12FILE
