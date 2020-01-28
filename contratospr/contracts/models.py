import cgi
from tempfile import TemporaryFile

import requests
from django.conf import settings
from django.contrib.postgres.fields import JSONField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.files import File
from django.db import models
from django.utils.module_loading import import_string
from django_extensions.db.fields import AutoSlugField

from ..utils.models import BaseModel
from ..utils.pdftotext import pdf_to_text
from .manager import ContractManager

document_storage = import_string(settings.CONTRACTS_DOCUMENT_STORAGE)()


def get_filename_from_content_disposition(value):
    _, parsed_header = cgi.parse_header(value)
    return parsed_header.get("filename", "")


def document_file_path(instance, filename):
    return f"documents/{instance.source_id}/{filename}"


class Entity(BaseModel):
    name = models.CharField(max_length=255)
    source_id = models.PositiveIntegerField(unique=True)
    slug = AutoSlugField(populate_from="name")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Entities"

    def __str__(self):
        return self.name


class ServiceGroup(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    slug = AutoSlugField(populate_from="name")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Service(BaseModel):
    name = models.CharField(max_length=255)
    group = models.ForeignKey("ServiceGroup", null=True, on_delete=models.SET_NULL)
    slug = AutoSlugField(populate_from="name")

    class Meta:
        ordering = ["name"]
        unique_together = ("name", "group")

    def __str__(self):
        return self.name


class Document(BaseModel):
    source_id = models.PositiveIntegerField(unique=True)
    source_url = models.URLField()
    file = models.FileField(
        blank=True, null=True, upload_to=document_file_path, storage=document_storage
    )

    pages = JSONField(blank=True, null=True)

    def __str__(self):
        return f"{self.source_id}"

    def download(self):
        with TemporaryFile() as temp_file:
            with requests.get(self.source_url, stream=True) as r:
                for chunk in r.iter_content(chunk_size=4096):
                    temp_file.write(chunk)
                temp_file.seek(0)

            content_disposition = r.headers.get("content-disposition", "")
            file_name = get_filename_from_content_disposition(content_disposition)

            self.file.save(file_name, File(temp_file))

    def detect_text(self):
        pages = []
        output = pdf_to_text(self.file)

        for number, page in enumerate(output.split(b"\f"), start=1):
            text = page.strip().decode("utf-8")

            if text:
                pages.append({"number": number, "text": text})

        if pages:
            self.pages = pages
            return self.save(update_fields=["pages"])


class Contractor(BaseModel):
    name = models.CharField(max_length=255)
    source_id = models.PositiveIntegerField(unique=True)
    entity_id = models.PositiveIntegerField(blank=True, null=True)
    slug = AutoSlugField(populate_from=["name", "source_id"])

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Contract(BaseModel):
    entity = models.ForeignKey("Entity", null=True, on_delete=models.SET_NULL)
    source_id = models.PositiveIntegerField(unique=True)
    number = models.CharField(max_length=255)
    amendment = models.CharField(max_length=255, blank=True, null=True)
    slug = AutoSlugField(populate_from=["number", "amendment", "source_id"])
    date_of_grant = models.DateTimeField()
    effective_date_from = models.DateTimeField(db_index=True)
    effective_date_to = models.DateTimeField(db_index=True)
    service = models.ForeignKey("Service", null=True, on_delete=models.SET_NULL)
    cancellation_date = models.DateTimeField(blank=True, null=True)
    amount_to_pay = models.DecimalField(max_digits=20, decimal_places=2)
    has_amendments = models.BooleanField()
    document = models.ForeignKey("Document", null=True, on_delete=models.SET_NULL)
    exempt_id = models.CharField(max_length=255)
    contractors = models.ManyToManyField("Contractor")
    parent = models.ForeignKey(
        "self", null=True, on_delete=models.CASCADE, related_name="amendments"
    )

    search_vector = SearchVectorField(null=True)

    objects = ContractManager()

    class Meta:
        indexes = [GinIndex(fields=["search_vector"])]

    def __str__(self):
        if self.amendment:
            return f"{self.number} - {self.amendment}"

        return f"{self.number}"
