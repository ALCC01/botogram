# Copyright (c) 2015-2017 The Botogram Authors (see AUTHORS)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER

import base64
import binascii
import hashlib

from . import crypto
from .context import ctx


DIGEST = hashlib.md5
DIGEST_LEN = 16


class ButtonsRow:
    """A row of an inline keyboard"""

    def __init__(self):
        self._content = []

    def url(self, label, url):
        """Open an URL when the button is pressed"""
        self._content.append({"text": label, "url": url})

    def callback(self, label, callback, data=None):
        """Trigger a callback when the button is pressed"""
        def generate_callback_data():
            c = ctx()

            name = "%s:%s" % (c.component_name(), callback)
            return get_callback_data(c.bot, c.chat(), name, data)

        self._content.append({
            "text": label,
            "callback_data": generate_callback_data,
        })

    def switch_inline_query(self, label, query="", current_chat=False):
        """Switch the user to this bot's inline query"""
        if current_chat:
            self._content.append({
                "text": label,
                "switch_inline_query_current_chat": query,
            })
        else:
            self._content.append({
                "text": label,
                "switch_inline_query": query,
            })

    def _get_content(self):
        """Get the content of this row"""
        for item in self._content:
            new = item.copy()

            # Replace any callable with its value
            # This allows to dynamically generate field values
            for key, value in new.items():
                if callable(value):
                    new[key] = value()

            yield new


class Buttons:
    """Factory for inline keyboards"""

    def __init__(self):
        self._rows = {}

    def __getitem__(self, index):
        if index not in self._rows:
            self._rows[index] = ButtonsRow()
        return self._rows[index]

    def _serialize_attachment(self):
        rows = [
            list(row._get_content()) for i, row in sorted(
                tuple(self._rows.items()), key=lambda i: i[0]
            )
        ]

        return {"inline_keyboard": rows}


def buttons():
    """Create a new inline keyboard"""
    return Buttons()


def parse_callback_data(bot, chat, raw):
    """Parse the callback data generated by botogram and return it"""
    raw = raw.encode("utf-8")

    if len(raw) < 32:
        raise crypto.TamperedMessageError

    try:
        prelude = base64.b64decode(raw[:32])
    except binascii.Error:
        raise crypto.TamperedMessageError

    signature = prelude[:16]
    name = prelude[16:]
    data = raw[32:]

    correct = get_signature(bot, chat, name, data)
    if not crypto.compare(correct, signature):
        raise crypto.TamperedMessageError

    if data:
        return name, data.decode("utf-8")
    else:
        return name, None


def get_callback_data(bot, chat, name, data=None):
    """Get the callback data for the provided name and data"""
    name = hashed_callback_name(name)

    if data is None:
        data = ""
    data = data.encode("utf-8")

    if len(data) > 32:
        raise ValueError(
            "The provided data is too big (%s bytes), try to reduce it to "
            "32 bytes" % len(data)
        )

    # Get the signature of the hook name and data
    signature = get_signature(bot, chat, name, data)

    # Base64 the signature and the hook name together to save space
    return (base64.b64encode(signature + name) + data).decode("utf-8")


def get_signature(bot, chat, name, data):
    """Generate a signature for the provided information"""
    chat_id = str(chat.id).encode("utf-8")
    return crypto.get_hmac(bot, name + b'\0' + chat_id + b'\0' + data)


def hashed_callback_name(name):
    """Get the hashed name of a callback"""
    # Get only the first 8 bytes of the hash to fit it into the payload
    return DIGEST(name.encode("utf-8")).digest()[:8]


def process(bot, chains, update):
    """Process a callback sent to the bot"""
    chat = update.callback_query.message.chat
    raw = update.callback_query._data

    try:
        name, data = parse_callback_data(bot, chat, raw)
    except crypto.TamperedMessageError:
        bot.logger.warn(
            "The user tampered with the #%s update's data. Skipped it."
            % update.update_id
        )
        return

    for hook in chains["callbacks"]:
        bot.logger.debug("Processing update #%s with the hook %s" %
                         (update.update_id, hook.name))

        result = hook.call(bot, update, name, data)
        if result is True:
            bot.logger.debug("Update #%s was just processed by the %s hook" %
                             (update.update_id, hook.name))
            return

    bot.logger.debug("No hook actually processed the #%s update." %
                     update.update_id)
