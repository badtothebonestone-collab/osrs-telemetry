package com.osrstelemetry;

public class EventRecord
{
    public String schemaVersion;
    public long tickId;
    public long eventSeq;
    public String timestampUtc;
    public String eventType;
    public Object payload;
}