from os import environ as env

from django.db import models
from django.conf import settings
from django.template import Context, Template

from twilio.rest import Client, TwilioException
from twilio.twiml.voice_response import VoiceResponse
import logging
import urllib
import time

from cabot.cabotapp.alert import AlertPlugin, AlertPluginUserData

# The retry count for verifying call status
RETRY_COUNT = 5

telephone_template = "This is an urgent message from Affirm monitoring. "   \
            "Service \"{{ service.name }}\" is facing an issue. "           \
            "Please check Cabot urgently."

sms_template = "Service {{ service.name }} "                                \
            "{% if service.overall_status == service.PASSING_STATUS %}"     \
            "is back to normal"                                             \
            "{% else %}"                                                    \
            "reporting {{ service.overall_status }} status"                 \
            "{% endif %}"                                                   \
            " : {{ scheme }}://{{ host }}{% url 'service' pk=service.id %}"

logger = logging.getLogger(__name__)


class TwilioPhoneCall(AlertPlugin):
    '''
    A twilio plugin which uses the twimlets service to make an alert call

    Using twimlets has the following advantages
    * Cabot can be hosted within an intranet (or pvt network). We do not
      have to expose the cabot endpoint to the internet
    * Can be used for development (where you are testing on localhost)
    '''
    name = "Twilio Phone Call"
    author = "Jonathan Balls"

    def send_alert(self, service, users, duty_officers):

        # No need to call to say things are resolved
        if service.overall_status != service.CRITICAL_STATUS:
            return

        account_sid = env.get('TWILIO_ACCOUNT_SID')
        auth_token = env.get('TWILIO_AUTH_TOKEN')
        outgoing_number = env.get('TWILIO_OUTGOING_NUMBER')

        # Create a twiml response
        message = VoiceResponse()
        ctx = Context({'service': service})
        text = Template(telephone_template).render(ctx)
        message.say(text, voice='woman')

        params = urllib.urlencode(dict(
            Twiml=message.to_xml(xml_declaration=False)))

        # Use the twimlets service
        url = 'http://twimlets.com/echo?' + params

        client = Client(account_sid, auth_token)

        # FIXME: `user` is in fact a `profile`
        mobiles = TwilioUserData.objects.filter(user__user__in=duty_officers)
        mobiles = [m.prefixed_phone_number for m in mobiles if m.phone_number]
        for mobile in mobiles:
            try:
                call = client.calls.create(
                    to=mobile,
                    from_=outgoing_number,
                    if_machine='Hangup',
                    url=url,
                    method='GET',
                )
                assert call.status == call.Status.QUEUED

                # Looks like this sleep is required for the twilio call
                # to come through
                count = RETRY_COUNT
                while count > 0:
                    time.sleep(5)
                    call = call.update()
                    logger.debug('Call status is: %s' % (call.status))
                    if call.status == call.Status.COMPLETED:
                        break
                    count -= 1

                assert call.status == call.Status.COMPLETED

                if call.answered_by == 'machine':
                    raise TwilioException('Call reached answering machine.')

            except Exception as e:
                logger.exception('Error making twilio phone call: %s' % e)
                raise


class TwilioSMS(AlertPlugin):
    name = "Twilio SMS"
    author = "Jonathan Balls"

    def send_alert(self, service, users, duty_officers):

        account_sid = env.get('TWILIO_ACCOUNT_SID')
        auth_token = env.get('TWILIO_AUTH_TOKEN')
        outgoing_number = env.get('TWILIO_OUTGOING_NUMBER')

        all_users = list(users) + list(duty_officers)

        client = Client(account_sid, auth_token)
        mobiles = TwilioUserData.objects.filter(user__user__in=all_users)
        mobiles = [m.prefixed_phone_number for m in mobiles if m.phone_number]
        c = Context({
            'service': service,
            'host': settings.WWW_HTTP_HOST,
            'scheme': settings.WWW_SCHEME,
        })
        message = Template(sms_template).render(c)
        for mobile in mobiles:
            try:
                client.messages.create(
                    to=mobile,
                    from_=outgoing_number,
                    body=message,
                )
            except Exception as e:
                logger.exception('Error sending twilio sms: %s' % e)
                raise


class TwilioUserData(AlertPluginUserData):
    name = "Twilio Plugin"
    phone_number = models.CharField(max_length=30, blank=True, null=True)

    def save(self, *args, **kwargs):
        if str(self.phone_number).startswith('+'):
            self.phone_number = self.phone_number[1:]
        return super(TwilioUserData, self).save(*args, **kwargs)

    @property
    def prefixed_phone_number(self):
        return '+%s' % self.phone_number
