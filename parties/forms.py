"""The party create/edit form: the two fields a manager may write."""

from django import forms

from .models import Party


class PartyForm(forms.ModelForm):
    """Name and description; ownership and roster are set elsewhere."""

    class Meta:
        model = Party
        fields = ["name", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "maxlength": 2000}),
        }
