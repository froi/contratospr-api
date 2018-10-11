import datetime
import re

import dramatiq
import pytz
from django.db import transaction

from .models import Contract, Contractor, Document, Entity, Service
from .scraper import BASE_CONTRACT_URL, get_amendments, get_contractors, get_contracts


def parse_date(value):
    if not value:
        return None

    ms = int(re.search("\d+", value).group())
    return datetime.datetime.utcfromtimestamp(ms // 1000).replace(tzinfo=pytz.UTC)


def strip_whitespace(value):
    return value.strip() if value else None


@dramatiq.actor
def expand_contract(contract):
    result = {
        "entity_id": contract["EntityId"],
        "entity_name": strip_whitespace(contract["EntityName"]),
        "contract_id": contract["ContractId"],
        "contract_number": contract["ContractNumber"],
        "amendment": contract["Amendment"],
        "date_of_grant": parse_date(contract["DateOfGrant"]),
        "effective_date_from": parse_date(contract["EffectiveDateFrom"]),
        "effective_date_to": parse_date(contract["EffectiveDateTo"]),
        "service": contract["Service"],
        "service_group": contract["ServiceGroup"],
        "cancellation_date": parse_date(contract["CancellationDate"]),
        "amount_to_pay": contract["AmountToPay"],
        "has_amendments": contract["HasAmendments"],
        "document_id": contract["DocumentWithoutSocialSecurityId"],
        "exempt_id": contract["ExemptId"],
        "contractors": [],
        "amendments": [],
    }

    if result["document_id"]:
        document_id = result["document_id"]
        result[
            "document_url"
        ] = f"{BASE_CONTRACT_URL}/downloaddocument?documentid={document_id}"

    contractors = get_contractors(result["contract_id"])

    for contractor in contractors:
        result["contractors"].append(
            {
                "contractor_id": contractor["ContractorId"],
                "entity_id": contractor["EntityId"],
                "name": contractor["Name"],
            }
        )

    if result["has_amendments"]:
        amendments = get_amendments(result["contract_number"], result["entity_id"])

        for amendment in amendments:
            result["amendments"].append(expand_contract(amendment))

    return result


@dramatiq.actor
@transaction.atomic
def update_contract(result, parent_id=None):
    entity, _ = Entity.objects.get_or_create(
        source_id=result["entity_id"], defaults={"name": result["entity_name"]}
    )

    service, _ = Service.objects.get_or_create(
        name=result["service"], group=result["service_group"]
    )

    contract_data = {
        "entity": entity,
        "number": result["contract_number"],
        "amendment": result["amendment"],
        "date_of_grant": result["date_of_grant"],
        "effective_date_from": result["effective_date_from"],
        "effective_date_to": result["effective_date_to"],
        "service": service,
        "cancellation_date": result["cancellation_date"],
        "amount_to_pay": result["amount_to_pay"],
        "has_amendments": result["has_amendments"],
        "exempt_id": result["exempt_id"],
        "parent_id": parent_id,
    }

    if result["document_id"]:
        document, _ = Document.objects.get_or_create(
            source_id=result["document_id"],
            defaults={"source_url": result["document_url"]},
        )

        contract_data["document"] = document

    contract, _ = Contract.objects.update_or_create(
        source_id=result["contract_id"], defaults=contract_data
    )

    for contractor_result in result["contractors"]:
        contractor, _ = Contractor.objects.get_or_create(
            source_id=contractor_result["contractor_id"],
            defaults={
                "name": contractor_result["name"],
                "entity_id": contractor_result["entity_id"],
            },
        )

        contract.contractors.add(contractor)

    for amendment_result in result["amendments"]:
        update_contract.send(amendment_result, parent_id=contract.pk)

    return contract.pk


@dramatiq.actor
def scrape_contracts(limit=100):
    offset = 0
    total_records = 0

    while offset <= total_records:
        contracts = get_contracts(offset, limit)

        if not total_records:
            total_records = limit if limit else contracts["recordsFiltered"]

        for contract in contracts["data"]:
            dramatiq.pipeline(
                [expand_contract.message(contract), update_contract.message()]
            ).run()

        offset += limit