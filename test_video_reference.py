import unittest
from bili_workbench.bili import parse_video_reference
from bili_workbench.runtime import Fault


class VideoReferenceTests(unittest.TestCase):
    def test_bv_and_bilibili_urls_are_parsed_without_network(self):
        self.assertEqual(parse_video_reference('BV1eRJS67EJP')['bvid'],'BV1eRJS67EJP')
        self.assertEqual(parse_video_reference('https://www.bilibili.com/video/BV1eRJS67EJP/?p=3')['part_index'],3)
        self.assertIsNone(parse_video_reference('https://m.bilibili.com/video/BV1eRJS67EJP')['part_index'])

    def test_rejects_untrusted_hosts_or_ambiguous_parts(self):
        for value in ('https://bilibili.com.evil.test/video/BV1eRJS67EJP','https://bilibili.com@evil.test/video/BV1eRJS67EJP','https://b23.tv/short','file:///video/BV1eRJS67EJP','https://www.bilibili.com:443/video/BV1eRJS67EJP','https://www.bilibili.com/video/BV1eRJS67EJP?p=1&p=2','https://www.bilibili.com/video/BV1eRJS67EJP?p=0'):
            with self.subTest(value=value),self.assertRaises(Fault):parse_video_reference(value)


if __name__=='__main__':unittest.main()
