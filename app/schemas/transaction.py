import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TransactionBase(BaseModel):
    numero_facture: str | None = None
    code_produit: str | None = None
    description_produit: str | None = None
    quantite: int
    prix_unitaire: float
    date_transaction: datetime
    pays: str | None = None
    est_retour: bool = False


class TransactionCreate(TransactionBase):
    client_id: uuid.UUID | None = None


class TransactionOut(TransactionBase):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: uuid.UUID
    client_id: uuid.UUID | None
