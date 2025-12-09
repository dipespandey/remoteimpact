from django import template

register = template.Library()


@register.filter(name="add_class")
def add_class(field, css):
    """
    Add CSS classes to a form field widget in templates.
    Usage: {{ field|add_class:"class1 class2" }}
    """
    return field.as_widget(attrs={"class": css})
