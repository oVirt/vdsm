#!/bin/bash

KEYFILE="server.key"
PASSKEYFILE="server.pass.key"
CSRFILE="server.csr"
CRTFILE="server.crt"

OTHERKEYFILE="other.key"
OTHERCSRFILE="other.csr"
OTHERCRTFILE="other.crt"

P12FILE="server.p12"
PASSWD="pass:secretpassphrase"

# hardcoded localhost is not working on all of the network configurations (m2c)
HOST=`hostname -I | cut -d' ' -f1`

openssl genrsa -des3 -passout $PASSWD -out $PASSKEYFILE 2048
openssl rsa -passin $PASSWD -in $PASSKEYFILE -out $KEYFILE
rm $PASSKEYFILE
openssl req -new -key $KEYFILE -out $CSRFILE -subj "/C=US/ST=Bar/L=Foo/O=Dis/CN=$HOST"
openssl x509 -req -days 365 -in $CSRFILE -signkey $KEYFILE -out $CRTFILE

openssl genrsa -des3 -passout $PASSWD -out $PASSKEYFILE 2048
openssl rsa -passin $PASSWD -in $PASSKEYFILE -out $OTHERKEYFILE
rm $PASSKEYFILE
openssl req -new -key $OTHERKEYFILE -out $OTHERCSRFILE -subj "/C=US/ST=Foo/L=Bar/O=Dis/CN=$HOST"
openssl x509 -req -days 365 -in $OTHERCSRFILE -signkey $OTHERKEYFILE -out $OTHERCRTFILE

openssl pkcs12 -passout $PASSWD -export -in $CRTFILE -inkey $KEYFILE -out $P12FILE
