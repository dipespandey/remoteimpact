"""Explicit trust boundaries for first-party template scripts and handlers."""
from html import escape, unescape
from html.parser import HTMLParser
import json
import re

from django import template
from django.utils.safestring import mark_safe
from jobs.csp import signature

register = template.Library()


def script(code, opening='<script>'):
    opening = opening[:-1] + ' data-ri-csp="' + signature('script', code) + '">'
    return opening + code + '</script>'


class SignedNode(template.Node):
    child_nodelists = ('nodelist',)

    def __init__(self, nodelist, kind, event=None):
        self.nodelist, self.kind, self.event = nodelist, kind, event

    def render(self, context):
        value = self.nodelist.render(context)
        if self.kind == 'script':
            match = re.fullmatch(r'(<script\b[^>]*>)(.*?)</script\s*>', value, re.S | re.I)
            if not match:
                raise template.TemplateSyntaxError('csp_script requires one script element')
            return mark_safe(script(match[2], match[1]))
        if self.kind == 'handler':
            code = unescape(value)
            return mark_safe(f'{self.event}="{escape(code, quote=True)}" data-ri-csp-{self.event}="{signature(self.event, code)}"')
        # A component factory runs ordinary authored JS; Alpine's CSP build only
        # resolves the registered name and never compiles arbitrary expressions.
        class Element(HTMLParser):
            def handle_starttag(self, tag, attrs):
                self.attrs = dict(attrs)
        parser = Element(); parser.feed(value)
        attrs = parser.attrs
        # HTMLParser already decodes attribute references, like the browser.
        expression = attrs.get('x-data', '').strip() or '{}'
        init = attrs.get('x-init', '')
        init = re.sub(r'^\s*selected\s*=', 'this.selected =', init)
        init = re.sub(r'^\s*([A-Za-z_$][\w$]*)\(\)\s*$', r'this.\1()', init)
        name = 'ri_' + signature('component', expression + '\n' + init)[:20]
        code = '(function(){function register(){Alpine.data(' + json.dumps(name) + ',function(){var value=(' + expression + ');'
        code += '''value._round=function(v){return Math.round(v);};
value._clampPercent=function(v){return Math.max(0,Math.min(100,v));};
value._posted=function(v){this.selected=v;this.open=false;this.$dispatch('change');};
value._toggle=function(v){this.selected=this.selected.includes(v)?this.selected.filter(x=>x!==v):this.selected.concat([v]);};
value._remove=function(v){this.selected=this.selected.filter(x=>x!==v);};
value._clear=function(){this.selected=[];this.$dispatch('change');};
value._close=function(id){this.open=false;var el=document.getElementById(id);if(el)el.focus();};
value._outside=function(){if(!this.pressedInside)this.open=false;};
value._setLo=function(event){this.loPos=+event.target.value;if(this.loPos>this.hiPos)this.loPos=this.hiPos;this.updateVals();};
value._setHi=function(event){this.hiPos=+event.target.value;if(this.hiPos<this.loPos)this.hiPos=this.loPos;this.updateVals();};
value._toggleFaq=function(i){this.showFaq[i]=!this.showFaq[i];};
if('filteredCountries' in value)Object.defineProperty(value,'unselectedCountries',{get:function(){return this.filteredCountries.filter(c=>!this.selected.includes(c));}});
'''
        if init:
            code += 'var original=value.init;value.init=function(){if(original)original.call(this);' + init + ';};'
        code += "return value;});}if(window.Alpine)register();else document.addEventListener('alpine:init',register,{once:true});})();"
        code = code.replace('</script', '<\\/script')
        value = re.sub(r'\bx-data\s*=\s*([\"\']).*?\1', 'x-data="' + name + '"', value, count=1, flags=re.S)
        value = re.sub(r'\bx-init\s*=\s*([\"\']).*?\1', '', value, count=1, flags=re.S)
        return mark_safe(script(code) + value)


def block(parser, token, kind, end, event=False):
    bits = token.split_contents()
    name = bits[1].strip('"') if event else None
    if event and (len(bits) != 2 or not re.fullmatch('on[a-z]+', name)):
        raise template.TemplateSyntaxError('csp_handler requires an event name')
    nodes = parser.parse((end,)); parser.delete_first_token()
    return SignedNode(nodes, kind, name)


@register.tag
def csp_script(parser, token):
    return block(parser, token, 'script', 'endcsp_script')


@register.tag
def csp_handler(parser, token):
    return block(parser, token, 'handler', 'endcsp_handler', event=True)


@register.tag
def csp_component(parser, token):
    return block(parser, token, 'component', 'endcsp_component')
