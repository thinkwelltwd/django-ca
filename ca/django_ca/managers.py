# This file is part of django-ca (https://github.com/mathiasertl/django-ca).
#
# django-ca is free software: you can redistribute it and/or modify it under the terms of the GNU
# General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# django-ca is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
# even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with django-ca. If not,
# see <http://www.gnu.org/licenses/>.

"""Django model managers."""


import pathlib
from typing import TYPE_CHECKING
from typing import Any
from typing import Generic
from typing import Iterable
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import TypeVar
from typing import Union
from typing import cast

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.hazmat.primitives.serialization import PrivateFormat
from cryptography.x509.oid import AuthorityInformationAccessOID

from django.core.files.base import ContentFile
from django.db import models
from django.urls import reverse
from django.utils.encoding import force_str

from . import ca_settings
from .extensions import Extension
from .extensions import IssuerAlternativeName
from .extensions import NameConstraints
from .profiles import Profile
from .profiles import profiles
from .signals import post_create_ca
from .signals import post_issue_cert
from .signals import pre_create_ca
from .subject import Subject
from .typehints import Expires
from .typehints import Literal
from .typehints import ParsableExtension
from .typehints import ParsableHash
from .typehints import ParsableSubject
from .typehints import PrivateKeyTypes
from .utils import ca_storage
from .utils import generate_private_key
from .utils import get_cert_builder
from .utils import int_to_hex
from .utils import parse_csr
from .utils import parse_expires
from .utils import parse_hash_algorithm
from .utils import validate_hostname
from .utils import validate_key_parameters

# https://mypy.readthedocs.io/en/latest/runtime_troubles.html
if TYPE_CHECKING:
    from .models import AcmeAccount
    from .models import AcmeAuthorization
    from .models import AcmeCertificate
    from .models import AcmeChallenge
    from .models import AcmeOrder
    from .models import Certificate
    from .models import CertificateAuthority
    from .querysets import CertificateAuthorityQuerySet
    from .querysets import CertificateQuerySet

    AcmeAccountManagerBase = models.Manager[AcmeAccount]
    AcmeAuthorizationManagerBase = models.Manager[AcmeAuthorization]
    AcmeCertificateManagerBase = models.Manager[AcmeCertificate]
    AcmeChallengeManagerBase = models.Manager[AcmeChallenge]
    AcmeOrderManagerBase = models.Manager[AcmeOrder]
    CertificateAuthorityManagerBase = models.Manager[CertificateAuthority]
    CertificateManagerBase = models.Manager[Certificate]

    X509CertMixinTypeVar = TypeVar("X509CertMixinTypeVar", CertificateAuthority, Certificate)
    QuerySetTypeVar = TypeVar("QuerySetTypeVar", CertificateAuthorityQuerySet, CertificateQuerySet)
else:
    AcmeAccountManagerBase = (
        AcmeAuthorizationManagerBase
    ) = (
        AcmeCertificateManagerBase
    ) = (
        AcmeChallengeManagerBase
    ) = AcmeOrderManagerBase = CertificateAuthorityManagerBase = CertificateManagerBase = models.Manager
    X509CertMixinTypeVar = TypeVar("X509CertMixinTypeVar")
    QuerySetTypeVar = TypeVar("QuerySetTypeVar")


class CertificateManagerMixin(Generic[X509CertMixinTypeVar, QuerySetTypeVar]):
    """Mixin for model managers."""

    if TYPE_CHECKING:
        # django-stubs (mypy plugin for Django) currently typehints queryset methods as returning a manager,
        # and does not know about queryset methods comming from the queryset. We typehint basic queryset
        # methods here, so that mypy knows that returned objects are querysets.
        #
        # The type overrides are because of the return type, as mypy thinks they should return a manager.
        #
        # pylint: disable=missing-function-docstring,unused-argument; just defining stubs here

        def all(self) -> QuerySetTypeVar:
            ...

        def get_queryset(self) -> QuerySetTypeVar:
            ...

        def filter(self, *args: Any, **kwargs: Any) -> QuerySetTypeVar:
            ...

        def exclude(self, *args: Any, **kwargs: Any) -> QuerySetTypeVar:
            ...

        def order_by(self, *fields: str) -> QuerySetTypeVar:
            ...

        def get_by_serial_or_cn(self, identifier: str) -> X509CertMixinTypeVar:
            ...

        def valid(self) -> QuerySetTypeVar:
            ...

    def get_common_extensions(
        self,
        issuer_url: Optional[str] = None,
        crl_url: Optional[Iterable[str]] = None,
        ocsp_url: Optional[str] = None,
    ) -> List[Tuple[bool, Union[x509.CRLDistributionPoints, x509.AuthorityInformationAccess]]]:
        """Add extensions potentially common to both CAs and certs."""

        extensions: List[Tuple[bool, Union[x509.CRLDistributionPoints, x509.AuthorityInformationAccess]]] = []
        if crl_url:
            urls = [x509.UniformResourceIdentifier(force_str(c)) for c in crl_url]
            dps = [
                x509.DistributionPoint(full_name=[c], relative_name=None, crl_issuer=None, reasons=None)
                for c in urls
            ]
            extensions.append((False, x509.CRLDistributionPoints(dps)))
        auth_info_access = []
        if ocsp_url:
            uri = x509.UniformResourceIdentifier(force_str(ocsp_url))
            auth_info_access.append(
                x509.AccessDescription(access_method=AuthorityInformationAccessOID.OCSP, access_location=uri)
            )
        if issuer_url:
            uri = x509.UniformResourceIdentifier(force_str(issuer_url))
            auth_info_access.append(
                x509.AccessDescription(
                    access_method=AuthorityInformationAccessOID.CA_ISSUERS, access_location=uri
                )
            )
        if auth_info_access:
            extensions.append((False, x509.AuthorityInformationAccess(auth_info_access)))
        return extensions

    def _extra_extensions(
        self,
        builder: x509.CertificateBuilder,
        extra_extensions: Iterable[Union["x509.Extension[x509.ExtensionType]", "Extension[Any, Any, Any]"]],
    ) -> x509.CertificateBuilder:
        for ext in extra_extensions:
            if isinstance(ext, x509.Extension):
                builder = builder.add_extension(ext.value, critical=ext.critical)
            elif isinstance(ext, Extension):
                builder = builder.add_extension(*ext.for_builder())
            else:
                raise ValueError("Cannot add extension of type %s" % type(ext).__name__)
        return builder


class CertificateAuthorityManager(
    CertificateManagerMixin["CertificateAuthority", "CertificateAuthorityQuerySet"],
    CertificateAuthorityManagerBase,
):
    """Model manager for the CertificateAuthority model."""

    if TYPE_CHECKING:
        # See CertificateManagerMixin for description on this branch
        #
        # pylint: disable=missing-function-docstring,unused-argument; just defining stubs here

        def acme(self) -> "CertificateAuthorityQuerySet":
            ...

        def default(self) -> "CertificateAuthority":
            ...

        def disabled(self) -> "CertificateAuthorityQuerySet":
            ...

        def enabled(self) -> "CertificateAuthorityQuerySet":
            ...

        def invalid(self) -> "CertificateAuthorityQuerySet":
            ...

        def usable(self) -> "CertificateAuthorityQuerySet":
            ...

    def init(
        self,
        name: str,
        subject: Union[ParsableSubject, Subject],
        expires: Expires = None,
        algorithm: ParsableHash = None,
        parent: Optional["CertificateAuthority"] = None,
        default_hostname: Optional[Union[bool, str]] = None,
        pathlen: Optional[int] = None,
        issuer_url: Optional[str] = None,
        issuer_alt_name: Union[str, IssuerAlternativeName] = "",
        crl_url: Optional[Iterable[str]] = None,
        ocsp_url: Optional[str] = None,
        ca_issuer_url: Optional[str] = None,
        ca_crl_url: Optional[Sequence[str]] = None,
        ca_ocsp_url: Optional[str] = None,
        name_constraints: Optional[Union[ParsableExtension, NameConstraints]] = None,
        password: Optional[bytes] = None,
        parent_password: Optional[Union[str, bytes]] = None,
        ecc_curve: Optional[ec.EllipticCurve] = None,
        key_type: Literal["RSA", "DSA", "ECC"] = "RSA",
        key_size: Optional[int] = None,
        extra_extensions: Optional[
            Iterable[Union["x509.Extension[x509.ExtensionType]", "Extension[Any, Any, Any]"]]
        ] = None,
        path: Union[pathlib.PurePath, str] = "ca",
        caa: str = "",
        website: str = "",
        terms_of_service: str = "",
        acme_enabled: bool = False,
        acme_requires_contact: bool = True,
    ) -> "CertificateAuthority":
        """Create a new certificate authority.

        Parameters
        ----------

        name : str
            The name of the CA. This is a human-readable string and is used for administrative purposes only.
        subject : dict or str or :py:class:`~django_ca.subject.Subject`
            Subject string, e.g. ``"/CN=example.com"`` or ``Subject("/CN=example.com")``. The value is
            actually passed to :py:class:`~django_ca.subject.Subject` if it is not already an instance of that
            class.
        expires : int or datetime or timedelta, optional
            When this certificate authority will expire, defaults to :ref:`CA_DEFAULT_EXPIRES
            <settings-ca-default-expires>`.
        algorithm : str or :py:class:`~cg:cryptography.hazmat.primitives.hashes.HashAlgorithm`, optional
            Hash algorithm used when signing the certificate, passed to
            :py:func:`~django_ca.utils.parse_hash_algorithm`. The default is the value of the
            :ref:`CA_DIGEST_ALGORITHM <settings-ca-digest-algorithm>` setting.
        parent : :py:class:`~django_ca.models.CertificateAuthority`, optional
            Parent certificate authority for the new CA. Passing this value makes the CA an intermediate
            authority.
        default_hostname : str, optional
            Override the URLconfigured with :ref:`CA_DEFAULT_HOSTNAME <settings-ca-default-hostname>` with a
            different hostname. Set to ``False`` to disable the hostname.
        pathlen : int, optional
            Value of the path length attribute for the :py:class:`~django_ca.extensions.BasicConstraints`
            extension.
        issuer_url : str
            URL for the DER/ASN1 formatted certificate that is signing certificates.
        issuer_alt_name : :py:class:`~django_ca.extensions.IssuerAlternativeName` or str, optional
            IssuerAlternativeName used when signing certificates. If the value is not an instance of
            :py:class:`~django_ca.extensions.IssuerAlternativeName`, it will be passed as argument to
            the constructor of the class.
        crl_url : list of str, optional
            CRL URLs used for certificates signed by this CA.
        ocsp_url : str, optional
            OCSP URL used for certificates signed by this CA. The default is no value, unless
            :ref:`CA_DEFAULT_HOSTNAME <settings-ca-default-hostname>` is set.
        ca_issuer_url : str, optional
            URL for the DER/ASN1 formatted certificate that is signing this CA. For intermediate CAs, this
            would usually be the ``issuer_url`` of the parent CA.
        ca_crl_url : list of str, optional
            CRL URLs used for this CA. This value is only meaningful for intermediate CAs.
        ca_ocsp_url : str, optional
            OCSP URL used for this CA. This value is only meaningful for intermediate CAs. The default is
            no value, unless :ref:`CA_DEFAULT_HOSTNAME <settings-ca-default-hostname>` is set.
        name_constraints : list of lists or :py:class:`~django_ca.extensions.NameConstraints`
            List of names that this CA can sign and/or cannot sign. The value is passed to
            :py:class:`~django_ca.extensions.NameConstraints` if the value is not already an instance of that
            class.
        password : bytes, optional
            Password to encrypt the private key with.
        parent_password : bytes, optional
            Password that the private key of the parent CA is encrypted with.
        ecc_curve : str or EllipticCurve, optional
            The elliptic curve to use for ECC type keys, passed verbatim to
            :py:func:`~django_ca.utils.parse_key_curve`.
        key_type: str, optional
            The type of private key to generate, must be one of ``"RSA"``, ``"DSA"`` or ``"ECC"``, with
            ``"RSA"`` being the default.
        key_size : int, optional
            Integer specifying the key size, must be a power of two (e.g. 2048, 4096, ...). Defaults to
            the :ref:`CA_DEFAULT_KEY_SIZE <settings-ca-default-key-size>`, unused if ``key_type="ECC"``.
        extra_extensions : list of :py:class:`cg:cryptography.x509.Extension` or \
                :py:class:`django_ca.extensions.base.Extension`, optional
            An optional list of additional extensions to add to the certificate.
        path : str or pathlib.PurePath, optional
            Where to store the CA private key (default ``ca``).
        caa : str, optional
            CAA identity. Note that this field is not yet currently used.
        website : str, optional
            URL to a human-readable website.
        terms_of_service : str, optional
            URL to the terms of service for the CA.
        acme_enabled : bool, optional
            Set to ``True`` to enable ACME support for this CA.
        acme_requires_contact : bool, optional
            Set to ``False`` if you do not want to force clients to register with an email address.

        Raises
        ------

        ValueError
            For various cases of wrong input data (e.g. ``key_size`` not being the power of two).
        PermissionError
            If the private key file cannot be written to disk.
        """
        # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements
        # NOTE: Already verified by KeySizeAction, so these checks are only for when the Python API is used
        #       directly.
        generate_key_args = validate_key_parameters(key_size, key_type, ecc_curve)
        algorithm = parse_hash_algorithm(algorithm)
        expires = parse_expires(expires)

        # Normalize extensions to django_ca.extensions.Extension subclasses
        if not isinstance(subject, Subject):
            subject = Subject(subject)
        if issuer_alt_name and not isinstance(issuer_alt_name, IssuerAlternativeName):
            issuer_alt_name = IssuerAlternativeName({"value": [issuer_alt_name]})
        if crl_url is None:
            crl_url = []

        serial = x509.random_serial_number()
        hex_serial = int_to_hex(serial)

        if default_hostname is None:
            default_hostname = ca_settings.CA_DEFAULT_HOSTNAME

        # If there is a default hostname, use it to compute some URLs from that
        if isinstance(default_hostname, str) and default_hostname != "":
            default_hostname = validate_hostname(default_hostname, allow_port=True)
            if parent:
                root_serial = parent.serial
            else:
                root_serial = hex_serial

            # Set OCSP urls
            if not ocsp_url:
                ocsp_path = reverse("django_ca:ocsp-cert-post", kwargs={"serial": hex_serial})
                ocsp_url = "http://%s%s" % (default_hostname, ocsp_path)
            if parent and not ca_ocsp_url:  # OCSP for CA only makes sense in intermediate CAs
                ocsp_path = reverse("django_ca:ocsp-ca-post", kwargs={"serial": root_serial})
                ca_ocsp_url = "http://%s%s" % (default_hostname, ocsp_path)

            # Set issuer path
            issuer_path = reverse("django_ca:issuer", kwargs={"serial": root_serial})
            if parent and not ca_issuer_url:
                ca_issuer_url = "http://%s%s" % (default_hostname, issuer_path)
            if not issuer_url:
                issuer_url = "http://%s%s" % (default_hostname, issuer_path)

            # Set CRL URLs
            if not crl_url:
                crl_path = reverse("django_ca:crl", kwargs={"serial": hex_serial})
                crl_url = ["http://%s%s" % (default_hostname, crl_path)]
            if parent and not ca_crl_url:  # CRL for CA only makes sense in intermediate CAs
                ca_crl_path = reverse("django_ca:ca-crl", kwargs={"serial": root_serial})
                ca_crl_url = ["http://%s%s" % (default_hostname, ca_crl_path)]

        pre_create_ca.send(
            sender=self.model,
            name=name,
            key_size=key_size,
            key_type=key_type,
            algorithm=algorithm,
            expires=expires,
            parent=parent,
            subject=subject,
            pathlen=pathlen,
            issuer_url=issuer_url,
            issuer_alt_name=issuer_alt_name,
            crl_url=crl_url,
            ocsp_url=ocsp_url,
            ca_issuer_url=ca_issuer_url,
            ca_crl_url=ca_crl_url,
            ca_ocsp_url=ca_ocsp_url,
            name_constraints=name_constraints,
            password=password,
            parent_password=parent_password,
            extra_extensions=extra_extensions,
            caa=caa,
            website=website,
            terms_of_service=terms_of_service,
            acme_enabled=acme_enabled,
            acme_requires_contact=acme_requires_contact,
        )

        # NOTE: Since priv_key_params is a union of tuples, mypy is unable to determine that all overloaded
        # variants for generate_private_key() potentially match, and thus matches the first variant. See:
        #   https://mypy.readthedocs.io/en/latest/more_types.html#type-checking-calls-to-overloads
        private_key = cast(PrivateKeyTypes, generate_private_key(*generate_key_args))
        public_key = private_key.public_key()

        builder = get_cert_builder(expires, serial=serial)
        builder = builder.public_key(public_key)
        subject = subject.name

        builder = builder.subject_name(subject)
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=pathlen), critical=True)
        builder = builder.add_extension(
            x509.KeyUsage(
                key_cert_sign=True,
                crl_sign=True,
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )

        subject_key_id = x509.SubjectKeyIdentifier.from_public_key(public_key)
        builder = builder.add_extension(subject_key_id, critical=False)

        if parent is None:
            builder = builder.issuer_name(subject)
            private_sign_key = private_key
            aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key)
        else:
            builder = builder.issuer_name(parent.pub.loaded.subject)
            private_sign_key = parent.key(parent_password)
            aki = parent.get_authority_key_identifier()
        builder = builder.add_extension(aki, critical=False)

        for critical, ext in self.get_common_extensions(ca_issuer_url, ca_crl_url, ca_ocsp_url):
            builder = builder.add_extension(ext, critical=critical)

        if name_constraints:
            if not isinstance(name_constraints, NameConstraints):
                name_constraints = NameConstraints(name_constraints)

            builder = builder.add_extension(*name_constraints.for_builder())

        if extra_extensions:
            builder = self._extra_extensions(builder, extra_extensions)

        certificate = builder.sign(
            private_key=private_sign_key, algorithm=algorithm, backend=default_backend()
        )

        # Normalize extensions for create()
        crl_url = "\n".join(crl_url)

        ca = self.model(
            name=name,
            issuer_url=issuer_url,
            issuer_alt_name=",".join(issuer_alt_name),
            ocsp_url=ocsp_url,
            crl_url=crl_url,
            parent=parent,
            caa_identity=caa,
            website=website,
            terms_of_service=terms_of_service,
            acme_enabled=acme_enabled,
            acme_requires_contact=acme_requires_contact,
        )
        ca.update_certificate(certificate)

        if password is None:
            encryption: serialization.KeySerializationEncryption = serialization.NoEncryption()
        else:
            encryption = serialization.BestAvailableEncryption(password)

        pem = private_key.private_bytes(
            encoding=Encoding.PEM, format=PrivateFormat.PKCS8, encryption_algorithm=encryption
        )

        # write private key to file
        path = path / pathlib.PurePath("%s.key" % ca.serial.replace(":", ""))
        ca.private_key_path = ca_storage.save(str(path), ContentFile(pem))
        ca.save()

        post_create_ca.send(sender=self.model, ca=ca)
        return ca


class CertificateManager(
    CertificateManagerMixin["Certificate", "CertificateQuerySet"], CertificateManagerBase
):
    """Model manager for the Certificate model."""

    if TYPE_CHECKING:
        # See CertificateManagerMixin for description on this branch
        #
        # pylint: disable=missing-function-docstring,unused-argument; just defining stubs here

        def expired(self) -> "CertificateQuerySet":
            ...

        def not_yet_valid(self) -> "CertificateQuerySet":
            ...

        def revoked(self) -> "CertificateQuerySet":
            ...

    def create_cert(
        self,
        ca: "CertificateAuthority",
        csr: Union[x509.CertificateSigningRequest, str, bytes],
        csr_format: Encoding = Encoding.PEM,
        profile: Optional[Union[str, Profile]] = None,
        autogenerated: Optional[bool] = None,
        **kwargs: Any,
    ) -> "Certificate":
        """Create and sign a new certificate based on the given profile.

        Parameters
        ----------

        ca : :py:class:`~django_ca.models.CertificateAuthority`
            The certificate authority to sign the certificate with.
        csr : str or :py:class:`~cg:cryptography.x509.CertificateSigningRequest`
            A valid CSR. If not already a :py:class:`~cg:cryptography.x509.CertificateSigningRequest`, the
            format is given by the ``csr_format`` parameter.
        csr_format : :py:class:`~cg:cryptography.hazmat.primitives.serialization.Encoding`, optional
            The format of the CSR. The default is ``PEM``.
        profile : str or :py:class:`~django_ca.profiles.Profile`, optional
            The name of a profile or a manually created :py:class:`~django_ca.profiles.Profile` instance. If
            not given, the profile configured by :ref:`CA_DEFAULT_PROFILE <settings-ca-default-profile>` is
            used.
        autogenerated : bool, optional
            Override the profiles ``autogenerated`` flag.
        **kwargs
            All other keyword arguments are passed to :py:func:`Profiles.create_cert()
            <django_ca.profiles.Profile.create_cert>`.
        """

        if not isinstance(profile, Profile):
            profile = profiles[profile]

        csr = parse_csr(csr, csr_format=csr_format)
        cert = profile.create_cert(ca, csr, **kwargs)

        obj = self.model(ca=ca, csr=csr.public_bytes(Encoding.PEM).decode("utf-8"), profile=profile.name)
        obj.update_certificate(cert)
        if autogenerated is None:
            obj.autogenerated = profile.autogenerated
        else:
            obj.autogenerated = autogenerated
        obj.save()

        post_issue_cert.send(sender=self.model, cert=obj)

        return obj


class AcmeAccountManager(AcmeAccountManagerBase):
    """Model manager for :py:class:`~django_ca.models.AcmeAccount`."""


class AcmeOrderManager(AcmeOrderManagerBase):
    """Model manager for :py:class:`~django_ca.models.AcmeOrder`."""


class AcmeAuthorizationManager(AcmeAuthorizationManagerBase):
    """Model manager for :py:class:`~django_ca.models.AcmeAuthorization`."""


class AcmeChallengeManager(AcmeChallengeManagerBase):
    """Model manager for :py:class:`~django_ca.models.AcmeChallenge`."""


class AcmeCertificateManager(AcmeCertificateManagerBase):
    """Model manager for :py:class:`~django_ca.models.AcmeCertificate`."""
