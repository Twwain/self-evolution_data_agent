package com.example;

import javax.persistence.*;

@Embeddable
public class Address {

    @Column(name = "street")
    private String street;

    @Column(name = "city")
    private String city;

    @Column(name = "zip_code", length = 10)
    private String zipCode;
}
