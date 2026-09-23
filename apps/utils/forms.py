"""Shared form base classes for the backoffice design system."""

import urllib.parse

from django import forms
from django.db.models import Q
from django.http import QueryDict

TAILWIND_INPUT_CLASS = (
    "w-full rounded-xl border border-gray-300 bg-white/50 px-4 py-2.5 text-sm "
    "focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500/25"
)
TAILWIND_CHECKBOX_CLASS = "h-4 w-4 shrink-0 cursor-pointer accent-orange-500"
TAILWIND_FILE_CLASS = (
    "w-full rounded-xl border border-gray-300 bg-white/50 px-3 py-2 text-sm text-gray-600 "
    "file:mr-3 file:rounded-lg file:border-0 file:bg-orange-50 file:px-3 file:py-1.5 "
    "file:text-sm file:font-semibold file:text-orange-600 hover:file:bg-orange-100 "
    "focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500/25"
)


def active_choices(model_class, current_id=None, **active_filters):
    """Return a queryset filtered by active_filters, always including current_id if set."""
    qs = model_class.objects.filter(**active_filters)
    if current_id:
        return qs | model_class.objects.filter(Q(pk=current_id))
    return qs


class StyledModelForm(forms.ModelForm):
    """ModelForm that applies the backoffice Tailwind design system to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            self._style_widget(field)
            self._apply_placeholder(name, field)

    def _style_widget(self, field):
        """Apply the appropriate Tailwind class to a field's widget."""
        if isinstance(field, forms.DateField):
            # explicit format so edit-form initials render in the date picker
            field.widget = forms.DateInput(
                attrs={"type": "date", "class": TAILWIND_INPUT_CLASS},
                format="%Y-%m-%d",
            )
            return
        widget = field.widget
        if widget.attrs.get("class"):
            return
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = TAILWIND_CHECKBOX_CLASS
        elif isinstance(widget, forms.FileInput):
            widget.attrs["class"] = TAILWIND_FILE_CLASS
        else:
            widget.attrs["class"] = TAILWIND_INPUT_CLASS
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)

    def _apply_placeholder(self, name, field):
        """Add a 'Select ...' placeholder to choice fields where appropriate."""
        if isinstance(field, forms.ModelChoiceField):
            field.empty_label = f"Select {field.label.lower()}..."
            return
        if not getattr(field, "choices", None) or not field.required:
            return
        model_field = self._get_model_field(name)
        if model_field is None or model_field.has_default():
            return
        if field.choices[0][0] != "":
            field.choices = [("", f"Select {field.label.lower()}..."), *field.choices]

    def _get_model_field(self, name):
        """Return the model field matching a form field name, or None."""
        try:
            return self._meta.model._meta.get_field(name)
        except Exception:
            return None


def add_formset_row(formset_class, prefix, post_data):
    """Return a bound formset with one extra (empty) row appended."""
    post_data = post_data.copy()
    total_forms = int(post_data.get(f"{prefix}-TOTAL_FORMS", 0))

    empty_form = formset_class(prefix=prefix).empty_form
    line_fields = list(empty_form.fields.keys())

    for field in line_fields:
        post_data[f"{prefix}-{total_forms}-{field}"] = ""

    post_data[f"{prefix}-TOTAL_FORMS"] = str(total_forms + 1)

    return formset_class(post_data, prefix=prefix)


def remove_formset_row(formset_class, prefix, post_data, index):
    """Return a bound formset with the row at `index` dropped."""
    total_forms = int(post_data.get(f"{prefix}-TOTAL_FORMS", 0))

    empty_form = formset_class(prefix=prefix).empty_form
    line_fields = list(empty_form.fields.keys())

    new_data = {}
    new_index = 0

    for i in range(total_forms):
        if i == index:
            continue
        for field in line_fields:
            new_data[f"{prefix}-{new_index}-{field}"] = post_data.get(f"{prefix}-{i}-{field}", "")
        new_index += 1

    new_data[f"{prefix}-TOTAL_FORMS"] = str(new_index)
    new_data[f"{prefix}-INITIAL_FORMS"] = post_data.get(f"{prefix}-INITIAL_FORMS", "0")
    new_data[f"{prefix}-MIN_NUM_FORMS"] = post_data.get(f"{prefix}-MIN_NUM_FORMS", "0")
    new_data[f"{prefix}-MAX_NUM_FORMS"] = post_data.get(f"{prefix}-MAX_NUM_FORMS", "1000")

    for key in post_data:
        if key.startswith(prefix + "-"):
            continue
        vals = post_data.getlist(key)
        if len(vals) == 1:
            new_data[key] = vals[0]
        else:
            new_data[key] = vals

    encoded = urllib.parse.urlencode(new_data, doseq=True)
    rebuilt = QueryDict(encoded, mutable=True)

    return formset_class(rebuilt, prefix=prefix)
