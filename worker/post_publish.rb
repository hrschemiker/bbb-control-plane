#!/usr/bin/env ruby
require "optparse"

options = {}
OptionParser.new do |parser|
  parser.on("-m MEETING_ID") { |value| options[:meeting_id] = value }
  parser.on("-f FORMAT") { |value| options[:format] = value }
end.parse!

exit 0 unless options[:format] == "video"
meeting_id = options[:meeting_id].to_s
abort "invalid meeting id" unless /\A[a-zA-Z0-9-]+\z/.match?(meeting_id)
exec("/usr/bin/python3", "/usr/local/lib/bcp-post-publish.py", meeting_id)
