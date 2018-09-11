import urllib
from os import environ as env

from twilio.twiml.voice_response import VoiceResponse

from cabot.cabotapp.alert import AlertPlugin
from cabot.plugin_test_utils import PluginTestCase
from mock import Mock, patch, call

from cabot.cabotapp.models import Service
from cabot_alert_twilio import models


class TestTwilioSMSAlerts(PluginTestCase):
    def setUp(self):
        super(TestTwilioSMSAlerts, self).setUp()

        self.alert = AlertPlugin.objects.get(title=models.TwilioSMS.name)
        self.service.alerts.add(self.alert)
        self.service.save()

        self.from_number = env.get('TWILIO_OUTGOING_NUMBER')
        self.user_phonenumber = '15554443333'
        self.userdata = models.TwilioUserData.objects.create(user=self.user.profile, phone_number=self.user_phonenumber)

    @patch('cabot_alert_twilio.models.Client')
    def test_passing_to_error(self, fake_client_class):
        self.transition_service(Service.PASSING_STATUS, Service.ERROR_STATUS)
        fake_client_class.return_value.messages.create.assert_called_with(
            body='Service Service reporting ERROR status : http://localhost/service/2194/',
            to='+15554443333',
            from_=self.from_number
        )

    @patch('cabot_alert_twilio.models.Client')
    def test_error_to_passing(self, fake_client_class):
        self.transition_service(Service.ERROR_STATUS, Service.PASSING_STATUS)
        fake_client_class.return_value.messages.create.assert_called_with(
            body='Service Service is back to normal : http://localhost/service/2194/',
            to='+15554443333',
            from_=self.from_number
        )

    @patch('cabot_alert_twilio.models.Client')
    def test_error_to_acked(self, fake_client_class):
        self.transition_service(Service.ERROR_STATUS, Service.ACKED_STATUS)
        self.assertFalse(fake_client_class.return_value.messages.create.called)


class TestTwilioPhoneCallAlerts(PluginTestCase):
    def setUp(self):
        super(TestTwilioPhoneCallAlerts, self).setUp()

        self.alert = AlertPlugin.objects.get(title=models.TwilioPhoneCall.name)
        self.service.alerts.add(self.alert)
        self.service.save()

        self.from_number = env.get('TWILIO_OUTGOING_NUMBER')

        numbers = ((self.user, '15554443333'),
                   (self.duty_officer, '15551112345'),
                   (self.fallback_officer, '15559994242'))
        for user, number in numbers:
            models.TwilioUserData.objects.create(user=user.profile, phone_number=number)

    def user_to_number(self, user):
        return models.TwilioUserData.objects.get(user__user=user).prefixed_phone_number

    def msg_to_url(self, msg, voice='woman'):
        # type: (str, str) -> str
        response = VoiceResponse()
        response.say(msg, voice=voice)

        endpoint = 'http://twimlets.com/echo?'
        return endpoint + urllib.urlencode(dict(Twiml=response.to_xml(xml_declaration=False)))

    @patch('cabot_alert_twilio.models.Client')
    def test_passing_to_warning(self, fake_client_class):
        self.transition_service(Service.PASSING_STATUS, Service.WARNING_STATUS)
        self.assertFalse(fake_client_class.return_value.calls.create.called)

    @patch('cabot_alert_twilio.models.Client')
    def test_passing_to_error(self, fake_client_class):
        self.transition_service(Service.PASSING_STATUS, Service.ERROR_STATUS)
        self.assertFalse(fake_client_class.return_value.calls.create.called)

    @patch('cabot_alert_twilio.models.Client')
    def test_critical_to_acked(self, fake_client_class):
        self.transition_service(Service.CRITICAL_STATUS, Service.ACKED_STATUS)
        self.assertFalse(fake_client_class.return_value.calls.create.called)

    @staticmethod
    def create_mocked_call(answer):
        # type: (bool) -> Mock
        twilio_call = Mock()
        twilio_call.status = 'queued'

        # enum
        twilio_call.Status.QUEUED = 'queued'
        twilio_call.Status.COMPLETED = 'completed'
        twilio_call.Status.NO_ANSWER = "no-answer"

        def update():
            twilio_call.status = twilio_call.Status.COMPLETED if answer else twilio_call.Status.NO_ANSWER
            return twilio_call
        twilio_call.update.side_effect = update

        return twilio_call

    @patch('cabot_alert_twilio.models.Client')
    def test_passing_to_critical_oncall_picks_up(self, fake_client_class):
        client = fake_client_class.return_value
        create_call = client.calls.create

        # both calls get answered (though cabot should stop after the first)
        create_call.side_effect = (self.create_mocked_call(True), self.create_mocked_call(True))

        self.transition_service(Service.PASSING_STATUS, Service.CRITICAL_STATUS)
        self.assertTrue(create_call.called)

        msg = 'This is an urgent message from Affirm monitoring. Service "Service" is facing an issue. ' \
              'Please check Cabot urgently.'
        create_call.assert_has_calls([
            call(url=self.msg_to_url(msg), to=self.user_to_number(self.duty_officer),
                 from_=self.from_number, method='GET', if_machine='Hangup'),
        ])

    @patch('cabot_alert_twilio.models.Client')
    def test_passing_to_critical_oncall_ignores(self, fake_client_class):
        client = fake_client_class.return_value
        create_call = client.calls.create

        # first caller won't answer, next one will
        create_call.side_effect = (self.create_mocked_call(False), self.create_mocked_call(True))

        self.transition_service(Service.PASSING_STATUS, Service.CRITICAL_STATUS)
        self.assertTrue(create_call.called)

        msg = 'This is an urgent message from Affirm monitoring. Service "Service" is facing an issue. ' \
              'Please check Cabot urgently.'
        create_call.assert_has_calls([
            call(url=self.msg_to_url(msg), to=self.user_to_number(self.duty_officer),
                 from_=self.from_number, method='GET', if_machine='Hangup'),
            call(url=self.msg_to_url(msg), to=self.user_to_number(self.fallback_officer),
                 from_=self.from_number, method='GET', if_machine='Hangup'),
        ])
